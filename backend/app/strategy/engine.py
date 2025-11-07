"""Strategy engine for real-time trading analysis."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.context.service import get_context_service
from app.ws.models import Settings, TradeTick, get_settings
from app.ws.routes import get_ws_module

from .models import (
    Candle,
    ContextAnalysis,
    MarketRegime,
    SessionState,
    StrategyEngineState,
    StrategyEvent,
    Timeframe,
)
from .scheduler import SessionScheduler

logger = logging.getLogger("strategy")


class StrategyEngine:
    """Main strategy engine coordinating trading analysis components."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        context_service=None,
        ws_module=None,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.context_service = context_service or get_context_service()
        self.ws_module = ws_module or get_ws_module()
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

        # Component state
        self._started = False
        self._shutdown_event = asyncio.Event()

        # Candle aggregation state
        self._candle_buffers: Dict[Timeframe, Dict[str, Any]] = {}
        self._active_timeframes: List[Timeframe] = [Timeframe.ONE_MINUTE, Timeframe.FIVE_MINUTES]

        # Event subscribers
        self._event_subscribers: Dict[str, List[Callable[[StrategyEvent], None]]] = defaultdict(list)

        # Session scheduler
        self.scheduler = SessionScheduler(now_provider=self._now_provider)

        # Initialize candle buffers
        self._initialize_candle_buffers()

    def _initialize_candle_buffers(self) -> None:
        """Initialize candle aggregation buffers for active timeframes."""
        for timeframe in self._active_timeframes:
            self._candle_buffers[timeframe] = {
                "current": None,
                "buffer": deque(maxlen=1000),  # Keep last 1000 candles
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "volume": 0.0,
                "trades": 0,
                "start_time": None,
            }

    async def startup(self) -> None:
        """Start the strategy engine and all components."""
        if self._started:
            return

        logger.info("Starting strategy engine")
        self._started = True
        self._shutdown_event.clear()

        # Start session scheduler
        await self.scheduler.startup()

        # Subscribe to trade stream
        await self._subscribe_to_streams()

        logger.info("Strategy engine started successfully")

    async def shutdown(self) -> None:
        """Shutdown the strategy engine and all components."""
        if not self._started:
            return

        logger.info("Shutting down strategy engine")
        self._started = False
        self._shutdown_event.set()

        # Shutdown session scheduler
        await self.scheduler.shutdown()

        logger.info("Strategy engine shut down")

    async def _subscribe_to_streams(self) -> None:
        """Subscribe to WebSocket streams for real-time data."""
        # The context service already ingests trades, so we'll process from there
        # We'll periodically poll for new data and aggregate candles
        asyncio.create_task(self._candle_aggregation_loop())

    async def _candle_aggregation_loop(self) -> None:
        """Background loop for aggregating trades into candles."""
        while self._started and not self._shutdown_event.is_set():
            try:
                await self._aggregate_candles()
                await asyncio.sleep(1.0)  # Check every second
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error in candle aggregation: %s", exc)
                await asyncio.sleep(5.0)  # Back off on error

    async def _aggregate_candles(self) -> None:
        """Aggregate recent trades into candles."""
        now = self._now_provider()

        for timeframe in self._active_timeframes:
            buffer_data = self._candle_buffers[timeframe]
            candle_duration = self._get_candle_duration(timeframe)

            # Initialize new candle if needed
            if buffer_data["start_time"] is None:
                buffer_data["start_time"] = self._align_to_timeframe(now, timeframe)
                buffer_data["open"] = None
                buffer_data["high"] = None
                buffer_data["low"] = None
                buffer_data["close"] = None
                buffer_data["volume"] = 0.0
                buffer_data["trades"] = 0
                continue

            # Check if candle is complete
            if now >= buffer_data["start_time"] + candle_duration:
                # Complete the current candle
                if buffer_data["open"] is not None:
                    candle = Candle(
                        timestamp=buffer_data["start_time"],
                        open=buffer_data["open"],
                        high=buffer_data["high"],
                        low=buffer_data["low"],
                        close=buffer_data["close"],
                        volume=buffer_data["volume"],
                        timeframe=timeframe,
                        trades=buffer_data["trades"],
                    )
                    buffer_data["buffer"].append(candle)
                    self._emit_event("candle_complete", {"candle": candle, "timeframe": timeframe})

                # Start new candle
                buffer_data["start_time"] = self._align_to_timeframe(now, timeframe)
                buffer_data["open"] = None
                buffer_data["high"] = None
                buffer_data["low"] = None
                buffer_data["close"] = None
                buffer_data["volume"] = 0.0
                buffer_data["trades"] = 0

    def _align_to_timeframe(self, timestamp: datetime, timeframe: Timeframe) -> datetime:
        """Align timestamp to the start of the timeframe."""
        if timeframe == Timeframe.ONE_MINUTE:
            return timestamp.replace(second=0, microsecond=0)
        elif timeframe == Timeframe.FIVE_MINUTES:
            minute = (timestamp.minute // 5) * 5
            return timestamp.replace(minute=minute, second=0, microsecond=0)
        else:
            return timestamp.replace(second=0, microsecond=0)

    def _get_candle_duration(self, timeframe: Timeframe) -> timedelta:
        """Get the duration for a given timeframe."""
        if timeframe == Timeframe.ONE_MINUTE:
            return timedelta(minutes=1)
        elif timeframe == Timeframe.FIVE_MINUTES:
            return timedelta(minutes=5)
        else:
            return timedelta(minutes=1)

    def ingest_trade(self, trade: TradeTick) -> None:
        """Ingest a trade tick into the strategy engine."""
        # Only process if we're in an active session
        if not self.scheduler.is_active_session():
            return

        for timeframe in self._active_timeframes:
            buffer_data = self._candle_buffers[timeframe]

            if buffer_data["start_time"] is None:
                continue

            # Update OHLCV
            price = float(trade.price)
            qty = float(trade.qty)

            if buffer_data["open"] is None:
                buffer_data["open"] = price
                buffer_data["high"] = price
                buffer_data["low"] = price
                buffer_data["close"] = price
            else:
                buffer_data["high"] = max(buffer_data["high"], price)
                buffer_data["low"] = min(buffer_data["low"], price)
                buffer_data["close"] = price

            buffer_data["volume"] += qty
            buffer_data["trades"] += 1

    def get_state(self) -> StrategyEngineState:
        """Get the current state of the strategy engine."""
        candle_buffers = {}
        for timeframe, buffer_data in self._candle_buffers.items():
            candles = list(buffer_data["buffer"])
            candle_buffers[timeframe.value] = [
                Candle.model_validate(c) for c in candles[-20:]  # Last 20 candles
            ]

        return StrategyEngineState(
            is_running=self._started,
            current_session=self.scheduler.get_current_session(),
            active_timeframes=self._active_timeframes.copy(),
            last_update=self._now_provider(),
            candle_buffers=candle_buffers,
        )

    def get_candles(self, timeframe: Timeframe, count: int = 100) -> List[Candle]:
        """Get recent candles for a specific timeframe."""
        buffer_data = self._candle_buffers.get(timeframe, {})
        candles = list(buffer_data.get("buffer", []))
        return [Candle.model_validate(c) for c in candles[-count:]]

    def subscribe_events(self, event_type: str, callback: Callable[[StrategyEvent], None]) -> None:
        """Subscribe to strategy events."""
        self._event_subscribers[event_type].append(callback)

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emit a strategy event to all subscribers."""
        event = StrategyEvent(
            timestamp=self._now_provider(),
            event_type=event_type,
            source="strategy_engine",
            data=data,
        )

        for callback in self._event_subscribers.get(event_type, []):
            try:
                callback(event)
            except Exception as exc:
                logger.exception("Error in event callback: %s", exc)


# Global instance
_engine_instance: Optional[StrategyEngine] = None


def get_strategy_engine() -> StrategyEngine:
    """Get the global strategy engine instance."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = StrategyEngine()
    return _engine_instance