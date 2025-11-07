"""Trade ingestion for Binance perpetual futures."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

from .client import BaseStreamService, structured_log
from .metrics import MetricsRecorder
from .models import Settings, TradeSide, TradeTick

if TYPE_CHECKING:
    from app.context.service import ContextService


def parse_trade_message(message: Dict[str, Any]) -> TradeTick:
    """Normalize a Binance aggTrade/trade payload."""

    if "p" not in message or "q" not in message:
        raise ValueError("unexpected trade payload: missing price or quantity")

    ts_ms = message.get("T") or message.get("E")
    if ts_ms is None:
        raise ValueError("trade payload missing timestamp")

    is_buyer_maker = bool(message.get("m"))
    side = TradeSide.SELL if is_buyer_maker else TradeSide.BUY
    trade_id = int(message.get("a") or message.get("t"))

    return TradeTick(
        ts=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
        price=float(message["p"]),
        qty=float(message["q"]),
        side=side,
        isBuyerMaker=is_buyer_maker,
        id=trade_id,
    )


class TradeStream(BaseStreamService):
    """Background service ingesting aggregated trades."""

    def __init__(
        self,
        settings: Settings,
        metrics: MetricsRecorder,
        context_service: Optional["ContextService"] = None,
    ) -> None:
        super().__init__("trades", settings.trades_ws_url or "", settings)
        self.metrics = metrics
        self.context_service = context_service
        self._strategy_engine = None

    def set_strategy_engine(self, strategy_engine) -> None:
        """Set the strategy engine reference for trade forwarding."""
        self._strategy_engine = strategy_engine

    async def handle_payload(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        event_type = str(payload.get("e", "")).lower()
        if event_type and event_type not in {"aggtrade", "trade"}:
            return
        try:
            tick = parse_trade_message(payload)
        except (ValueError, KeyError) as exc:
            structured_log(
                self.logger,
                "trade_skip",
                stream=self.name,
                reason=str(exc),
            )
            return

        self.state.last_ts = tick.ts
        self.metrics.record_trade()
        if self.context_service:
            self.context_service.ingest_trade(tick)
        
        # Forward to strategy engine if available
        if self._strategy_engine:
            self._strategy_engine.ingest_trade(tick)
        
        lag_ms = (datetime.now(timezone.utc) - tick.ts).total_seconds() * 1000
        structured_log(
            self.logger,
            "trade_tick",
            price=tick.price,
            qty=tick.qty,
            side=tick.side.value,
            lag_ms=round(lag_ms, 2),
            queue_size=self.queue_size,
        )
