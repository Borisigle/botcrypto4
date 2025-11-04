"""Context service providing trading session metrics and levels."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import polars as pl

from app.ws.models import Settings, TradeSide, TradeTick, get_settings

logger = logging.getLogger("context")


class ContextService:
    """Aggregates live trade data to provide contextual trading metrics."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

        self._started = False
        self.prev_day_levels: Dict[str, Optional[float]] = {
            "PDH": None,
            "PDL": None,
            "VAHprev": None,
            "VALprev": None,
            "POCprev": None,
        }

        self.current_day: Optional[date] = None
        self.day_start: Optional[datetime] = None
        self.or_start: Optional[datetime] = None
        self.or_end: Optional[datetime] = None

        self.vwap_numerator: float = 0.0
        self.vwap_denominator: float = 0.0
        self.day_high: Optional[float] = None
        self.day_low: Optional[float] = None
        self.pre_market_delta: float = 0.0
        self.volume_by_price: defaultdict[float, float] = defaultdict(float)
        self.poc_price: Optional[float] = None
        self.poc_volume: float = 0.0
        self.total_volume: float = 0.0
        self.or_high: Optional[float] = None
        self.or_low: Optional[float] = None

    async def startup(self) -> None:
        if self._started:
            return
        self._started = True
        today = self._now_provider().date()
        self._roll_day(today)
        if self.settings.context_bootstrap_prev_day:
            prev_day = today - timedelta(days=1)
            levels = self._load_previous_day(prev_day)
            if levels:
                self.prev_day_levels.update(levels)

    async def shutdown(self) -> None:
        self._started = False

    def ingest_trade(self, trade: TradeTick) -> None:
        trade_ts = trade.ts.astimezone(timezone.utc)
        trade_day = trade_ts.date()

        if self.current_day is None or trade_day != self.current_day:
            self._roll_day(trade_day)

        if not self.day_start or trade_ts < self.day_start or trade_ts >= self.day_start + timedelta(days=1):
            # Ignore trades that do not belong to the active session window
            return

        price = float(trade.price)
        qty = float(trade.qty)
        if qty <= 0:
            return

        self.total_volume += qty
        self.vwap_numerator += price * qty
        self.vwap_denominator += qty

        if self.day_high is None or price > self.day_high:
            self.day_high = price
        if self.day_low is None or price < self.day_low:
            self.day_low = price

        if self.or_start and self.or_end and self.or_start <= trade_ts < self.or_end:
            if self.or_high is None or price > self.or_high:
                self.or_high = price
            if self.or_low is None or price < self.or_low:
                self.or_low = price

        if self.or_start and trade_ts < self.or_start:
            delta = qty if trade.side == TradeSide.BUY else -qty
            self.pre_market_delta += delta

        volume = self.volume_by_price[price] + qty
        self.volume_by_price[price] = volume
        if (volume > self.poc_volume) or (
            abs(volume - self.poc_volume) <= 1e-9 and (self.poc_price is None or price < self.poc_price)
        ):
            self.poc_price = price
            self.poc_volume = volume

    def context_payload(self) -> Dict[str, Any]:
        now = self._now_provider()
        return {
            "session": self._session_state(now),
            "levels": self.levels_payload(),
            "stats": self.stats_payload(),
        }

    def levels_payload(self) -> Dict[str, Any]:
        or_start_iso = self.or_start.isoformat() if self.or_start else None
        or_end_iso = self.or_end.isoformat() if self.or_end else None
        vwap_value = self._current_vwap()

        levels = {
            "OR": {
                "hi": self.or_high,
                "lo": self.or_low,
                "startTs": or_start_iso,
                "endTs": or_end_iso,
            },
            "VWAP": vwap_value,
            "PDH": self.prev_day_levels.get("PDH"),
            "PDL": self.prev_day_levels.get("PDL"),
            "VAHprev": self.prev_day_levels.get("VAHprev"),
            "VALprev": self.prev_day_levels.get("VALprev"),
            "POCd": self.poc_price,
            "POCprev": self.prev_day_levels.get("POCprev"),
        }
        return levels

    def stats_payload(self) -> Dict[str, Optional[float]]:
        range_today = None
        if self.day_high is not None and self.day_low is not None:
            range_today = self.day_high - self.day_low
        return {
            "rangeToday": range_today,
            "cd_pre": self.pre_market_delta,
        }

    def _session_state(self, now: datetime) -> Dict[str, Any]:
        current_time = now.timetz()
        start_london = time(hour=8, tzinfo=timezone.utc)
        start_overlap = time(hour=12, tzinfo=timezone.utc)
        end_overlap = time(hour=16, minute=30, tzinfo=timezone.utc)

        state = "off"
        if start_london <= current_time < start_overlap:
            state = "london"
        elif start_overlap <= current_time < end_overlap:
            state = "overlap"

        return {"state": state, "nowUtc": now.isoformat()}

    def _roll_day(self, new_day: date) -> None:
        if self.current_day is not None and self.total_volume > 0:
            snapshot_map = dict(self.volume_by_price)
            prev_levels = self._profile_from_volume(snapshot_map, self.day_high, self.day_low)
            if prev_levels:
                self.prev_day_levels.update(prev_levels)

        self.current_day = new_day
        self.day_start = datetime.combine(new_day, time(0, tzinfo=timezone.utc))
        self.or_start = self.day_start + timedelta(hours=8)
        self.or_end = self.or_start + timedelta(minutes=10)
        self.vwap_numerator = 0.0
        self.vwap_denominator = 0.0
        self.day_high = None
        self.day_low = None
        self.pre_market_delta = 0.0
        self.volume_by_price = defaultdict(float)
        self.poc_price = None
        self.poc_volume = 0.0
        self.total_volume = 0.0
        self.or_high = None
        self.or_low = None

    def _current_vwap(self) -> Optional[float]:
        if self.vwap_denominator <= 0:
            return None
        return self.vwap_numerator / self.vwap_denominator

    def _load_previous_day(self, prev_day: date) -> Dict[str, Optional[float]]:
        history_dir = Path(self.settings.context_history_dir).expanduser()
        history_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self.settings.symbol_lower}_{prev_day.isoformat()}.parquet"
        candidate = history_dir / filename
        if not candidate.exists():
            if self.settings.context_fetch_missing_history:
                logger.warning("context_prev_day_missing", extra={"path": str(candidate)})
            return {}

        try:
            df = pl.read_parquet(candidate)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("failed_loading_prev_day", extra={"error": str(exc), "path": str(candidate)})
            return {}

        columns = {col.lower(): col for col in df.columns}
        price_col = columns.get("price") or columns.get("p")
        qty_col = columns.get("qty") or columns.get("quantity") or columns.get("volume")
        if not price_col or not qty_col:
            logger.warning(
                "context_prev_day_columns_missing",
                extra={"path": str(candidate), "columns": df.columns},
            )
            return {}

        prices = df[price_col].to_list()
        qtys = df[qty_col].to_list()
        volume_map: Dict[float, float] = {}
        day_high: Optional[float] = None
        day_low: Optional[float] = None
        for price_raw, qty_raw in zip(prices, qtys):
            price = float(price_raw)
            qty = float(qty_raw)
            volume_map[price] = volume_map.get(price, 0.0) + qty
            if day_high is None or price > day_high:
                day_high = price
            if day_low is None or price < day_low:
                day_low = price

        return self._profile_from_volume(volume_map, day_high, day_low)

    def _profile_from_volume(
        self,
        volume_map: Dict[float, float],
        day_high: Optional[float],
        day_low: Optional[float],
    ) -> Dict[str, Optional[float]]:
        if not volume_map:
            return {}

        total_volume = sum(volume_map.values())
        if total_volume <= 0:
            return {}

        poc_price, poc_volume = max(
            volume_map.items(), key=lambda item: (item[1], -item[0])
        )

        sorted_by_volume = sorted(volume_map.items(), key=lambda item: (-item[1], item[0]))
        cumulative = 0.0
        selected_prices: set[float] = set()
        for price, vol in sorted_by_volume:
            selected_prices.add(price)
            cumulative += vol
            if cumulative >= total_volume * 0.7:
                break

        vah = max(selected_prices) if selected_prices else None
        val = min(selected_prices) if selected_prices else None

        if day_high is None:
            day_high = max(volume_map.keys())
        if day_low is None:
            day_low = min(volume_map.keys())

        return {
            "PDH": day_high,
            "PDL": day_low,
            "VAHprev": vah,
            "VALprev": val,
            "POCprev": poc_price,
        }


_service_instance: Optional[ContextService] = None


def get_context_service() -> ContextService:
    global _service_instance
    if _service_instance is None:
        _service_instance = ContextService()
    return _service_instance
