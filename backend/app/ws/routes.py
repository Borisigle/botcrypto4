"""FastAPI routes and startup wiring for websocket ingestion."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict

from fastapi import APIRouter

from .depth import DepthStream
from .metrics import MetricsRecorder
from .models import MetricsSnapshot, StreamHealth, get_settings
from .trades import TradeStream

router = APIRouter()
logger = logging.getLogger(__name__)


class WSModule:
    """Coordinates lifecycle for trade and depth ingestion."""

    def __init__(self) -> None:
        self.settings = get_settings()
        logging.getLogger().setLevel(self.settings.log_level)
        self.metrics = MetricsRecorder(self.settings.metrics_window_sec)
        self._strategy_engine = None

        # Binance WebSocket mode (default)
        self.trade_stream = TradeStream(
            self.settings,
            self.metrics,
        )
        self.depth_stream = DepthStream(self.settings, self.metrics)

    def set_strategy_engine(self, strategy_engine) -> None:
        """Set the strategy engine reference for trade forwarding."""
        self._strategy_engine = strategy_engine
        if self.trade_stream:
            self.trade_stream.set_strategy_engine(strategy_engine)

    async def startup(self) -> None:
        # Binance WebSocket mode
        if self.trade_stream:
            await self.trade_stream.start()
        if self.depth_stream:
            await self.depth_stream.start()

    async def shutdown(self) -> None:
        if self.trade_stream:
            await self.trade_stream.stop()
        if self.depth_stream:
            await self.depth_stream.stop()

    def health_payload(self) -> Dict[str, Dict[str, Any]]:
        return {
            "trades": self._serialize_health(self.trade_stream.health()) if self.trade_stream else {"connected": False, "last_ts": None},
            "depth": self._serialize_health(self.depth_stream.health()) if self.depth_stream else {"connected": False, "last_ts": None},
        }

    def metrics_payload(self) -> Dict[str, Any]:
        snapshot: MetricsSnapshot = self.metrics.snapshot(
            trade_queue_size=self.trade_stream.queue_size if self.trade_stream else 0,
            depth_queue_size=self.depth_stream.queue_size if self.depth_stream else 0,
        )
        return snapshot.model_dump()

    @staticmethod
    def _serialize_health(health: StreamHealth) -> Dict[str, Any]:
        return health.model_dump()


@lru_cache(maxsize=1)
def get_ws_module() -> WSModule:
    return WSModule()


@router.get("/ws/health")
async def ws_health() -> Dict[str, Any]:
    module = get_ws_module()
    return module.health_payload()


@router.get("/metrics")
async def metrics() -> Dict[str, Any]:
    module = get_ws_module()
    return module.metrics_payload()