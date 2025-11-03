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


class WSModule:
    """Coordinates lifecycle for trade and depth ingestion."""

    def __init__(self) -> None:
        self.settings = get_settings()
        logging.getLogger().setLevel(self.settings.log_level)
        self.metrics = MetricsRecorder(self.settings.metrics_window_sec)
        self.trade_stream = TradeStream(self.settings, self.metrics)
        self.depth_stream = DepthStream(self.settings, self.metrics)

    async def startup(self) -> None:
        await self.trade_stream.start()
        await self.depth_stream.start()

    async def shutdown(self) -> None:
        await self.trade_stream.stop()
        await self.depth_stream.stop()

    def health_payload(self) -> Dict[str, Dict[str, Any]]:
        return {
            "trades": self._serialize_health(self.trade_stream.health()),
            "depth": self._serialize_health(self.depth_stream.health()),
        }

    def metrics_payload(self) -> Dict[str, Any]:
        snapshot: MetricsSnapshot = self.metrics.snapshot(
            trade_queue_size=self.trade_stream.queue_size,
            depth_queue_size=self.depth_stream.queue_size,
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
