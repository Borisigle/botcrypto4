"""Volume Delta service implementation."""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Deque, List, Optional, Sequence, Union

from app.models.indicators import VolumeDeltaSnapshot
from app.models.trade import Trade

TradeLike = Union[Trade, dict]


class VolumeDeltaService:
    """Service responsible for calculating and tracking Volume Delta snapshots."""

    def __init__(
        self,
        period_seconds: int = 60,
        history_limit: int = 1000,
    ) -> None:
        self.period_seconds = period_seconds
        self._history_limit = history_limit
        self._lock = Lock()
        self.delta_history: Deque[VolumeDeltaSnapshot] = deque(maxlen=history_limit)
        self.logger = logging.getLogger("volume_delta_service")

    def calculate_volume_delta(
        self,
        trades: Sequence[TradeLike],
        period_seconds: Optional[int] = None,
    ) -> dict:
        """Calculate volume delta for a specific time period."""
        if period_seconds is None:
            period_seconds = self.period_seconds

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=period_seconds)
        
        period_trades = self._filter_trades_since_time(trades, cutoff)
        
        buy_volume, sell_volume = self._calculate_volumes(period_trades)
        volume_delta = buy_volume - sell_volume
        
        return {
            "period": period_seconds,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "volume_delta": volume_delta,
            "trade_count": len(period_trades),
            "timestamp": now,
        }

    def record_snapshot(self, delta_data: dict) -> VolumeDeltaSnapshot:
        """Save snapshot to history."""
        snapshot = VolumeDeltaSnapshot(
            period=delta_data["period"],
            buy_volume=delta_data["buy_volume"],
            sell_volume=delta_data["sell_volume"],
            volume_delta=delta_data["volume_delta"],
            trade_count=delta_data["trade_count"],
            timestamp=delta_data["timestamp"],
        )
        
        with self._lock:
            self.delta_history.append(snapshot)
        
        return snapshot

    def get_history(self, period: Optional[int] = None, limit: int = 100) -> List[VolumeDeltaSnapshot]:
        """Return most recent Volume Delta snapshots, optionally filtered by period."""
        if limit <= 0:
            return []

        with self._lock:
            history = list(self.delta_history)

        # Filter by period if specified
        if period is not None:
            history = [s for s in history if s.period == period]

        return history[-limit:]

    def _filter_trades_since_time(
        self,
        trades: Sequence[TradeLike],
        cutoff_time: datetime,
    ) -> List[TradeLike]:
        filtered: List[TradeLike] = []
        for trade in trades:
            trade_time = self._extract_trade_time(trade)
            if trade_time is None:
                continue
            if trade_time >= cutoff_time:
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
    def _calculate_volumes(trades: Sequence[TradeLike]) -> tuple[float, float]:
        buy_volume = 0.0
        sell_volume = 0.0
        for trade in trades:
            side = VolumeDeltaService._extract_trade_side(trade)
            qty = VolumeDeltaService._extract_trade_qty(trade)
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


_volume_delta_service: Optional[VolumeDeltaService] = None


def init_volume_delta_service(period_seconds: int = 60) -> VolumeDeltaService:
    global _volume_delta_service
    if _volume_delta_service is None:
        _volume_delta_service = VolumeDeltaService(period_seconds=period_seconds)
    else:
        _volume_delta_service.period_seconds = period_seconds
    return _volume_delta_service


def get_volume_delta_service() -> VolumeDeltaService:
    if _volume_delta_service is None:
        raise RuntimeError("VolumeDeltaService has not been initialized yet")
    return _volume_delta_service