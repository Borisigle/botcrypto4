"""FastAPI routes and startup wiring for websocket ingestion."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, Optional

from fastapi import APIRouter

from app.context.service import get_context_service
from app.data_sources.hft_connector import HFTConnectorStream

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
        self.context_service = get_context_service()
        self._strategy_engine = None
        self._connector_stream: Optional[HFTConnectorStream] = None

        # Initialize based on data source configuration
        if self.settings.data_source.lower() == "hft_connector":
            # Connector mode will be initialized on demand
            self.trade_stream = None
            self.depth_stream = None
        else:
            # Binance WebSocket mode (default)
            self.trade_stream = TradeStream(
                self.settings,
                self.metrics,
                context_service=self.context_service,
            )
            self.depth_stream = DepthStream(self.settings, self.metrics)

    def set_strategy_engine(self, strategy_engine) -> None:
        """Set the strategy engine reference for trade forwarding."""
        self._strategy_engine = strategy_engine
        if self.trade_stream:
            self.trade_stream.set_strategy_engine(strategy_engine)
        if self._connector_stream:
            # For connector streams, trades are queued and processed separately
            pass

    async def startup(self) -> None:
        if self.settings.data_source.lower() == "hft_connector":
            # Initialize connector mode on startup
            await self._setup_connector_mode()
        else:
            # Binance WebSocket mode
            await self.trade_stream.start()
            await self.depth_stream.start()

    async def shutdown(self) -> None:
        if self.settings.data_source.lower() == "hft_connector":
            if self._connector_stream:
                await self._connector_stream.stop()
        else:
            if self.trade_stream:
                await self.trade_stream.stop()
            if self.depth_stream:
                await self.depth_stream.stop()

    async def _setup_connector_mode(self) -> None:
        """Setup connector mode with stubbed connector for testing."""
        from app.data_sources.hft_connector import StubbedConnector

        connector = StubbedConnector(self.settings)
        self._connector_stream = HFTConnectorStream(
            self.settings,
            connector,
            self.metrics,
            context_service=self.context_service,
        )
        # Set strategy engine if available
        if self._strategy_engine:
            self._connector_stream.set_strategy_engine(self._strategy_engine)
        await self._connector_stream.start()

    def health_payload(self) -> Dict[str, Dict[str, Any]]:
        if self.settings.data_source.lower() == "hft_connector":
            if self._connector_stream:
                health = self._connector_stream.health()
                return {
                    "connector": self._serialize_health(health),
                }
            return {"connector": {"connected": False, "last_ts": None}}
        else:
            return {
                "trades": self._serialize_health(self.trade_stream.health()) if self.trade_stream else {"connected": False, "last_ts": None},
                "depth": self._serialize_health(self.depth_stream.health()) if self.depth_stream else {"connected": False, "last_ts": None},
            }

    def metrics_payload(self) -> Dict[str, Any]:
        if self.settings.data_source.lower() == "hft_connector":
            queue_size = self._connector_stream.queue_size if self._connector_stream else 0
            snapshot: MetricsSnapshot = self.metrics.snapshot(
                trade_queue_size=queue_size,
                depth_queue_size=queue_size,
            )
        else:
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
