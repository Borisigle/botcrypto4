"""CVD (Cumulative Volume Delta) service implementation."""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, Iterable, List, Optional, Sequence, Tuple, Union

from app.models.indicators import CVDSnapshot
from app.models.trade import Trade

TradeLike = Union[Trade, dict]


class CVDService:
    """Service responsible for calculating and tracking CVD snapshots."""

    def __init__(
        self,
        reset_period_seconds: int = 3600,
        history_limit: int = 1000,
    ) -> None:
        self.reset_period_seconds = reset_period_seconds
        self._history_limit = history_limit
        self._lock = Lock()
        self._last_reset_time = datetime.now(timezone.utc)
        self.cvd_history: Deque[CVDSnapshot] = deque(maxlen=history_limit)
        self.logger = logging.getLogger("cvd_service")

    @property
    def last_reset_time(self) -> datetime:
        with self._lock:
            return self._last_reset_time

    def build_snapshot(
        self,
        trades: Sequence[TradeLike],
        record_history: bool = True,
    ) -> CVDSnapshot:
        """Build a CVDSnapshot for the provided trades."""

        reset_time = self.last_reset_time
        filtered_trades = self._filter_trades_since_reset(trades, reset_time)
        buy_volume, sell_volume = self._calculate_volumes(filtered_trades)
        volume_delta = buy_volume - sell_volume
        now = datetime.now(timezone.utc)
        seconds_since_reset = max((now - reset_time).total_seconds(), 0.0)

        snapshot = CVDSnapshot(
            cvd=volume_delta,
            reset_time=reset_time,
            seconds_since_reset=seconds_since_reset,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            volume_delta=volume_delta,
        )

        if record_history:
            with self._lock:
                self.cvd_history.append(snapshot)

        return snapshot

    def get_history(self, limit: int = 100) -> List[CVDSnapshot]:
        """Return most recent CVD snapshots."""

        if limit <= 0:
            return []

        with self._lock:
            history = list(self.cvd_history)

        return history[-limit:]

    def reset_cvd(self, reason: str = "manual") -> None:
        """Reset the CVD period manually."""

        now = datetime.now(timezone.utc)
        with self._lock:
            self._last_reset_time = now

        self.logger.info("CVD reset: reason=%s, reset_time=%s", reason, now.isoformat())

    def maybe_reset(self) -> bool:
        """Automatically reset if the reset period has elapsed."""

        need_reset = False
        with self._lock:
            elapsed = (datetime.now(timezone.utc) - self._last_reset_time).total_seconds()
            if elapsed >= self.reset_period_seconds:
                need_reset = True

        if need_reset:
            self.reset_cvd(reason="auto")
            return True
        return False

    def _filter_trades_since_reset(
        self,
        trades: Sequence[TradeLike],
        reset_time: datetime,
    ) -> List[TradeLike]:
        filtered: List[TradeLike] = []
        for trade in trades:
            trade_time = self._extract_trade_time(trade)
            if trade_time is None:
                continue
            if trade_time >= reset_time:
                filtered.append(trade)
        return filtered

    @staticmethod
    def _extract_trade_time(trade: TradeLike) -> Optional[datetime]:
        if isinstance(trade, Trade):
            ts = trade.time
        else:
            ts = trade.get("time")

        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

        if isinstance(ts, str):
            try:
                normalized = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
                dt = datetime.fromisoformat(normalized)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    @staticmethod
    def _calculate_volumes(trades: Iterable[TradeLike]) -> Tuple[float, float]:
        buy_volume = 0.0
        sell_volume = 0.0
        for trade in trades:
            side = CVDService._extract_trade_side(trade)
            qty = CVDService._extract_trade_qty(trade)
            if qty <= 0:
                continue
            if side == "buy":
                buy_volume += qty
            elif side == "sell":
                sell_volume += qty
        return buy_volume, sell_volume

    @staticmethod
    def _extract_trade_side(trade: TradeLike) -> str:
        if isinstance(trade, Trade):
            return trade.side.lower()
        side = trade.get("side")
        return str(side).lower() if side else ""

    @staticmethod
    def _extract_trade_qty(trade: TradeLike) -> float:
        if isinstance(trade, Trade):
            qty = trade.qty
        else:
            qty = trade.get("qty")
        try:
            return float(qty)
        except (TypeError, ValueError):
            return 0.0


_cvd_service: Optional[CVDService] = None


def init_cvd_service(reset_period_seconds: int = 3600) -> CVDService:
    global _cvd_service
    if _cvd_service is None:
        _cvd_service = CVDService(reset_period_seconds=reset_period_seconds)
    else:
        _cvd_service.reset_period_seconds = reset_period_seconds
    return _cvd_service


def get_cvd_service() -> CVDService:
    if _cvd_service is None:
        raise RuntimeError("CVDService has not been initialized yet")
    return _cvd_service
