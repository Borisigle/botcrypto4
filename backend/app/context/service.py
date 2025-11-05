"""Context service providing trading session metrics and levels."""
from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR, getcontext
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import httpx
import polars as pl

from app.ws.models import Settings, TradeSide, TradeTick, get_settings

from .backfill import BinanceTradeHistory, TradeHistoryProvider

logger = logging.getLogger("context")

getcontext().prec = 28

PERIODIC_LOG_SECONDS = 600


@dataclass(slots=True)
class SymbolExchangeInfo:
    symbol: str
    tick_size: Optional[float]
    step_size: Optional[float]
    min_qty: Optional[float]
    min_notional: Optional[float]
    raw: Dict[str, Any]


class ContextService:
    """Aggregates live trade data to provide contextual trading metrics."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
        exchange_info: Optional[SymbolExchangeInfo] = None,
        history_provider: Optional[TradeHistoryProvider] = None,
        fetch_exchange_info: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._fetch_exchange_info_enabled = fetch_exchange_info
        self._history_provider: Optional[TradeHistoryProvider] = history_provider

        self._started = False
        self._periodic_task: Optional[asyncio.Task[None]] = None
        self._exchange_info_logged = False

        self.exchange_info: Optional[SymbolExchangeInfo] = exchange_info
        self.tick_size: Optional[float] = exchange_info.tick_size if exchange_info else None
        self.step_size: Optional[float] = exchange_info.step_size if exchange_info else None
        self.min_qty: Optional[float] = exchange_info.min_qty if exchange_info else None
        self.min_notional: Optional[float] = exchange_info.min_notional if exchange_info else None

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

        self.sum_price_qty_base: float = 0.0
        self.sum_qty_base: float = 0.0
        self.sum_price_qty_quote: float = 0.0
        self.sum_qty_quote: float = 0.0
        self.trade_count: int = 0

        self.day_high: Optional[float] = None
        self.day_low: Optional[float] = None
        self.pre_market_delta: float = 0.0
        self.volume_by_price: defaultdict[float, float] = defaultdict(float)
        self.poc_price: Optional[float] = None
        self.poc_volume: float = 0.0
        self.top_volume_bins: list[tuple[float, float]] = []
        self.total_volume: float = 0.0
        self.or_high: Optional[float] = None
        self.or_low: Optional[float] = None
        self.last_trade_price: Optional[float] = None
        self.last_trade_ts: Optional[datetime] = None
        self.last_trade_snapshot: Optional[Dict[str, Any]] = None
        self.first_trade_snapshot: Optional[Dict[str, Any]] = None
        self.anchor_window_trades: list[Dict[str, Any]] = []

    async def startup(self) -> None:
        if self._started:
            return
        self._started = True

        if self.exchange_info is None and self._fetch_exchange_info_enabled:
            await self._ensure_exchange_info()

        if self.exchange_info is not None and not self._exchange_info_logged:
            self._log_exchange_info(self.exchange_info)
        elif self.exchange_info is None:
            logger.warning("exchange_info_unavailable symbol=%s", self.settings.symbol)

        now = self._now_provider()
        today = now.date()
        self._roll_day(today)

        prev_levels_loaded = False
        if self.settings.context_backfill_enabled:
            try:
                prev_levels_loaded = await self._perform_backfill(now)
            except Exception as exc:  # pragma: no cover - diagnostic logging
                logger.exception(
                    "context_backfill_failed",
                    extra={"error": str(exc)},
                )

        if not prev_levels_loaded and self.settings.context_bootstrap_prev_day:
            prev_day = today - timedelta(days=1)
            levels = self._load_previous_day(prev_day)
            if levels:
                self.prev_day_levels.update(levels)

        if self._periodic_task is None:
            self._periodic_task = asyncio.create_task(self._periodic_log_loop())

    async def shutdown(self) -> None:
        self._started = False
        if self._periodic_task is not None:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            self._periodic_task = None

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

        self.trade_count += 1
        self.total_volume += qty

        snapshot = self._snapshot_trade(trade_ts, price, qty)
        if self.first_trade_snapshot is None:
            self.first_trade_snapshot = snapshot
        self.last_trade_snapshot = snapshot

        day_start = self.day_start
        if day_start and day_start <= trade_ts < day_start + timedelta(minutes=5):
            if len(self.anchor_window_trades) < 5:
                self.anchor_window_trades.append(snapshot)
                logger.info(
                    "anchor_trade #%d ts=%s price=%.2f qty=%.6f",
                    len(self.anchor_window_trades),
                    snapshot["ts"],
                    price,
                    qty,
                )

        self.last_trade_price = price
        self.last_trade_ts = trade_ts

        base_notional = price * qty
        quote_volume = base_notional
        self.sum_price_qty_base += base_notional
        self.sum_qty_base += qty
        self.sum_price_qty_quote += price * quote_volume
        self.sum_qty_quote += quote_volume

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

        price_bin = self._bin_price(price)
        volume = self.volume_by_price[price_bin] + qty
        self.volume_by_price[price_bin] = volume
        self._update_poc_state(price_bin, volume)

    def context_payload(self, vwap_mode: str = "base") -> Dict[str, Any]:
        mode = self._normalize_vwap_mode(vwap_mode)
        now = self._now_provider()
        return {
            "session": self._session_state(now),
            "levels": self.levels_payload(mode),
            "stats": self.stats_payload(),
            "price": self.price_payload(),
        }

    def price_payload(self) -> Dict[str, Any]:
        ts_iso = self.last_trade_ts.isoformat() if self.last_trade_ts else None
        payload: Dict[str, Any] = {
            "price": self.last_trade_price,
            "ts": ts_iso,
        }
        symbol = getattr(self.settings, "symbol", None)
        if symbol:
            payload["symbol"] = symbol
        return payload

    def levels_payload(self, vwap_mode: str = "base") -> Dict[str, Any]:
        mode = self._normalize_vwap_mode(vwap_mode)
        or_start_iso = self.or_start.isoformat() if self.or_start else None
        or_end_iso = self.or_end.isoformat() if self.or_end else None
        vwap_value = self._current_vwap(mode)

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

    def debug_vwap_payload(self) -> Dict[str, Any]:
        anchor_iso = self.day_start.isoformat() if self.day_start else None
        return {
            "anchor": anchor_iso,
            "sum_price_qty": self.sum_price_qty_base,
            "sum_qty": self.sum_qty_base,
            "vwap": self._current_vwap("base"),
            "trade_count": self.trade_count,
            "first_trade": self.first_trade_snapshot,
            "last_trade": self.last_trade_snapshot,
        }

    def debug_poc_payload(self) -> Dict[str, Any]:
        top_bins = [
            {"price": price, "volume": volume, "rank": idx + 1}
            for idx, (price, volume) in enumerate(self.top_volume_bins)
        ]
        return {
            "bin_size": self.tick_size,
            "top_bins": top_bins,
            "poc_price": self.poc_price,
            "poc_volume": self.poc_volume,
        }

    def debug_exchange_info_payload(self) -> Dict[str, Any]:
        if not self.exchange_info:
            return {
                "symbol": self.settings.symbol,
                "error": "exchange info unavailable",
            }
        payload = {
            "symbol": self.exchange_info.symbol,
            "tickSize": self.exchange_info.tick_size,
            "stepSize": self.exchange_info.step_size,
            "minQty": self.exchange_info.min_qty,
            "minNotional": self.exchange_info.min_notional,
        }
        payload["raw"] = self.exchange_info.raw
        return payload

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
        self.sum_price_qty_base = 0.0
        self.sum_qty_base = 0.0
        self.sum_price_qty_quote = 0.0
        self.sum_qty_quote = 0.0
        self.trade_count = 0
        self.day_high = None
        self.day_low = None
        self.pre_market_delta = 0.0
        self.volume_by_price = defaultdict(float)
        self.poc_price = None
        self.poc_volume = 0.0
        self.top_volume_bins = []
        self.total_volume = 0.0
        self.or_high = None
        self.or_low = None
        self.last_trade_price = None
        self.last_trade_ts = None
        self.last_trade_snapshot = None
        self.first_trade_snapshot = None
        self.anchor_window_trades = []

    def _current_vwap(self, mode: str = "base") -> Optional[float]:
        normalized = self._normalize_vwap_mode(mode)
        if normalized == "quote":
            if self.sum_qty_quote <= 0:
                return None
            return self.sum_price_qty_quote / self.sum_qty_quote
        if self.sum_qty_base <= 0:
            return None
        return self.sum_price_qty_base / self.sum_qty_base

    @staticmethod
    def _normalize_vwap_mode(mode: Optional[str]) -> str:
        if isinstance(mode, str) and mode.lower() == "quote":
            return "quote"
        return "base"

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
            price_bin = self._bin_price(price)
            volume_map[price_bin] = volume_map.get(price_bin, 0.0) + qty
            if day_high is None or price > day_high:
                day_high = price
            if day_low is None or price < day_low:
                day_low = price

        return self._profile_from_volume(volume_map, day_high, day_low)

    def _get_history_provider(self) -> Optional[TradeHistoryProvider]:
        if self._history_provider is None:
            self._history_provider = BinanceTradeHistory(self.settings)
        return self._history_provider

    async def _perform_backfill(self, now: datetime) -> bool:
        provider = self._get_history_provider()
        if provider is None or not self.day_start:
            return False

        prev_levels_loaded = False
        day_start = self.day_start

        if now > day_start:
            start_iso = day_start.isoformat()
            end_iso = now.isoformat()
            logger.info(
                "Backfill: downloading trades from %s to %s",
                start_iso,
                end_iso,
            )
            try:
                trade_count = await self._ingest_historical_trades(provider, day_start, now)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception(
                    "backfill_today_failed",
                    extra={"error": str(exc), "start": start_iso, "end": end_iso},
                )
            else:
                vwap_value = self._current_vwap("base")
                range_today = (
                    self.day_high - self.day_low
                    if self.day_high is not None and self.day_low is not None
                    else None
                )
                logger.info(
                    "Backfill complete: trades=%d VWAP=%s POC=%s rangeToday=%s cd_pre=%s",
                    trade_count,
                    self._format_float(vwap_value),
                    self._format_float(self.poc_price),
                    self._format_float(range_today),
                    self._format_float(self.pre_market_delta),
                )
        else:
            logger.info("Backfill: startup at 00:00 UTC; skipping intraday history")

        try:
            prev_levels_loaded = await self._populate_previous_day(provider, day_start)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "backfill_previous_day_failed",
                extra={"error": str(exc)},
            )
            prev_levels_loaded = False

        return prev_levels_loaded

    async def _ingest_historical_trades(
        self,
        provider: TradeHistoryProvider,
        start: datetime,
        end: datetime,
    ) -> int:
        trade_count = 0
        progress_step = 50000
        async for trade in provider.iterate_trades(start, end):
            self.ingest_trade(trade)
            trade_count += 1
            if trade_count % progress_step == 0:
                logger.info(
                    "Backfill progress: trades=%d last_ts=%s",
                    trade_count,
                    trade.ts.isoformat(),
                )
        return trade_count

    async def _populate_previous_day(
        self,
        provider: TradeHistoryProvider,
        day_start: datetime,
    ) -> bool:
        prev_start = day_start - timedelta(days=1)
        prev_end = day_start - timedelta(milliseconds=1)
        if prev_end < prev_start:
            return False

        logger.info(
            "Backfill: loading previous day from %s to %s",
            prev_start.isoformat(),
            prev_end.isoformat(),
        )
        levels, trades_count, total_volume = await self._collect_previous_day_levels(
            provider,
            prev_start,
            prev_end,
        )
        if trades_count == 0 or not levels:
            logger.warning("Backfill previous day returned no trades")
            return False

        self.prev_day_levels.update(levels)
        logger.info(
            "Backfill previous day complete: trades=%d volume=%s PDH=%s PDL=%s VAH=%s VAL=%s POC=%s",
            trades_count,
            self._format_float(total_volume),
            self._format_float(levels.get("PDH")),
            self._format_float(levels.get("PDL")),
            self._format_float(levels.get("VAHprev")),
            self._format_float(levels.get("VALprev")),
            self._format_float(levels.get("POCprev")),
        )
        return True

    async def _collect_previous_day_levels(
        self,
        provider: TradeHistoryProvider,
        start: datetime,
        end: datetime,
    ) -> Tuple[Dict[str, Optional[float]], int, float]:
        volume_map: defaultdict[float, float] = defaultdict(float)
        day_high: Optional[float] = None
        day_low: Optional[float] = None
        total_trades = 0
        total_volume = 0.0
        progress_step = 50000

        async for trade in provider.iterate_trades(start, end):
            price = float(trade.price)
            qty = float(trade.qty)
            if qty <= 0:
                continue
            price_bin = self._bin_price(price)
            volume_map[price_bin] += qty
            total_volume += qty
            total_trades += 1
            if day_high is None or price > day_high:
                day_high = price
            if day_low is None or price < day_low:
                day_low = price
            if total_trades % progress_step == 0:
                logger.info(
                    "Backfill previous day progress: trades=%d last_price=%s",
                    total_trades,
                    self._format_float(price),
                )

        levels = self._profile_from_volume(dict(volume_map), day_high, day_low)
        return levels, total_trades, total_volume

    @staticmethod
    def _format_float(value: Optional[float]) -> str:
        if value is None:
            return "None"
        return f"{value:.6f}"

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

        poc_price, poc_volume = max(volume_map.items(), key=lambda item: (item[1], -item[0]))

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

    def _bin_price(self, price: float) -> float:
        tick = self.tick_size
        if not tick or tick <= 0:
            return price
        price_dec = Decimal(str(price))
        tick_dec = Decimal(str(tick))
        bins = (price_dec / tick_dec).to_integral_value(rounding=ROUND_FLOOR)
        return float(bins * tick_dec)

    def _update_poc_state(self, price_bin: float, volume: float) -> None:
        if (
            self.poc_price is None
            or volume > self.poc_volume
            or (
                math.isclose(volume, self.poc_volume, rel_tol=1e-12, abs_tol=1e-12)
                and price_bin < self.poc_price
            )
        ):
            self.poc_price = price_bin
            self.poc_volume = volume

        sorted_bins = sorted(self.volume_by_price.items(), key=lambda item: (-item[1], item[0]))
        self.top_volume_bins = sorted_bins[:10]

    @staticmethod
    def _snapshot_trade(ts: datetime, price: float, qty: float) -> Dict[str, Any]:
        return {
            "ts": ts.isoformat(),
            "price": price,
            "qty": qty,
        }

    async def _ensure_exchange_info(self) -> None:
        try:
            info = await self._fetch_exchange_info()
        except Exception as exc:  # pragma: no cover - logging safeguard
            logger.exception("exchange_info_fetch_failed symbol=%s error=%s", self.settings.symbol, exc)
            return

        if info is None:
            return
        self._apply_exchange_info(info)

    async def _fetch_exchange_info(self) -> Optional[SymbolExchangeInfo]:
        base_url = self.settings.rest_base_url.rstrip("/")
        endpoint = f"{base_url}/fapi/v1/exchangeInfo"
        symbol = self.settings.symbol.upper()
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=5.0)) as client:
            response = await client.get(endpoint, params={"symbol": symbol})
            response.raise_for_status()
        data = response.json()
        symbols = data.get("symbols") or []
        symbol_entry: Optional[Dict[str, Any]] = None
        for entry in symbols:
            if isinstance(entry, dict) and entry.get("symbol") == symbol:
                symbol_entry = entry
                break
        if not symbol_entry:
            logger.warning("exchange_info_symbol_missing symbol=%s", symbol)
            return None

        filters = {
            f.get("filterType"): f
            for f in symbol_entry.get("filters", [])
            if isinstance(f, dict) and f.get("filterType")
        }

        price_filter = filters.get("PRICE_FILTER")
        lot_size_filter = filters.get("LOT_SIZE")
        min_notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL")

        tick_size = self._safe_float(price_filter, "tickSize")
        step_size = self._safe_float(lot_size_filter, "stepSize")
        min_qty = self._safe_float(lot_size_filter, "minQty")
        min_notional_value = self._safe_float(min_notional_filter, "notional")
        if min_notional_value is None:
            min_notional_value = self._safe_float(min_notional_filter, "minNotional")

        return SymbolExchangeInfo(
            symbol=symbol,
            tick_size=tick_size,
            step_size=step_size,
            min_qty=min_qty,
            min_notional=min_notional_value,
            raw=symbol_entry,
        )

    @staticmethod
    def _safe_float(payload: Optional[Dict[str, Any]], key: str) -> Optional[float]:
        if not payload:
            return None
        value = payload.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):  # pragma: no cover - defensive parsing
            return None

    def _apply_exchange_info(self, info: SymbolExchangeInfo) -> None:
        self.exchange_info = info
        self.tick_size = info.tick_size
        self.step_size = info.step_size
        self.min_qty = info.min_qty
        self.min_notional = info.min_notional
        self._log_exchange_info(info)

    def _log_exchange_info(self, info: SymbolExchangeInfo) -> None:
        logger.info(
            "exchange_info_loaded symbol=%s tickSize=%s stepSize=%s minQty=%s minNotional=%s",
            info.symbol,
            info.tick_size,
            info.step_size,
            info.min_qty,
            info.min_notional,
        )
        self._exchange_info_logged = True

    async def _periodic_log_loop(self) -> None:
        try:
            while self._started:
                await asyncio.sleep(PERIODIC_LOG_SECONDS)
                if not self._started:
                    break
                self._log_snapshot()
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            pass

    def _log_snapshot(self) -> None:
        if not self.day_start:
            return
        now = self._now_provider()
        if not self._within_active_session(now):
            return
        vwap_value = self._current_vwap("base")
        anchor_iso = self.day_start.isoformat()
        vwap_repr = f"{vwap_value:.10f}" if vwap_value is not None else "None"
        poc_repr = f"{self.poc_price:.10f}" if self.poc_price is not None else "None"
        logger.info(
            "context_snapshot anchor=%s vwap=%s poc=%s sum_pv=%.10f sum_v=%.10f trades=%d",
            anchor_iso,
            vwap_repr,
            poc_repr,
            self.sum_price_qty_base,
            self.sum_qty_base,
            self.trade_count,
        )

    def _within_active_session(self, now: datetime) -> bool:
        if not self.day_start:
            return False
        return self.day_start <= now < self.day_start + timedelta(days=1)


_service_instance: Optional[ContextService] = None


def get_context_service() -> ContextService:
    global _service_instance
    if _service_instance is None:
        _service_instance = ContextService()
    return _service_instance
