"""FastAPI routes and startup wiring for websocket ingestion."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from ..connectors.bybit_websocket import BybitWebSocketStream
from ..services.trade_service import TradeService
from .depth import DepthStream
from .metrics import MetricsRecorder
from .models import MetricsSnapshot, StreamHealth, get_settings
from .trades import TradeStream

router = APIRouter(prefix="/ws")
logger = logging.getLogger(__name__)


class WSModule:
    """Coordinates lifecycle for trade and depth ingestion."""

    def __init__(self) -> None:
        self.settings = get_settings()
        logging.getLogger().setLevel(self.settings.log_level)
        self.metrics = MetricsRecorder(self.settings.metrics_window_sec)
        self._strategy_engine = None

        # Initialize trade service (shared buffer for all streams)
        self.trade_service = TradeService(self.settings)

        # Initialize trade stream based on data source
        self.trade_stream: Optional[TradeStream] = None
        self.bybit_trade_stream: Optional[BybitWebSocketStream] = None
        
        if self.settings.data_source == "bybit_ws":
            self.bybit_trade_stream = BybitWebSocketStream(
                self.settings,
                self.metrics,
            )
            # Connect trade service to the stream
            self.bybit_trade_stream.set_trade_service(self.trade_service)
        else:
            # Default: Binance WebSocket
            self.trade_stream = TradeStream(
                self.settings,
                self.metrics,
            )
            # Connect trade service to the stream
            self.trade_stream.set_trade_service(self.trade_service)
            
        self.depth_stream = DepthStream(self.settings, self.metrics)

    def set_strategy_engine(self, strategy_engine) -> None:
        """Set the strategy engine reference for trade forwarding."""
        self._strategy_engine = strategy_engine
        if self.trade_stream:
            self.trade_stream.set_strategy_engine(strategy_engine)
        if self.bybit_trade_stream:
            self.bybit_trade_stream.set_strategy_engine(strategy_engine)

    async def startup(self) -> None:
        # Start appropriate trade stream based on data source
        if self.trade_stream:
            await self.trade_stream.start()
        if self.bybit_trade_stream:
            await self.bybit_trade_stream.start()
        if self.depth_stream:
            await self.depth_stream.start()

    async def shutdown(self) -> None:
        if self.trade_stream:
            await self.trade_stream.stop()
        if self.bybit_trade_stream:
            await self.bybit_trade_stream.stop()
        if self.depth_stream:
            await self.depth_stream.stop()

    def health_payload(self) -> Dict[str, Dict[str, Any]]:
        # Get health from active trade stream
        if self.bybit_trade_stream:
            trades_health = self._serialize_health(self.bybit_trade_stream.health())
        elif self.trade_stream:
            trades_health = self._serialize_health(self.trade_stream.health())
        else:
            trades_health = {"connected": False, "last_ts": None}
            
        return {
            "trades": trades_health,
            "depth": self._serialize_health(self.depth_stream.health()) if self.depth_stream else {"connected": False, "last_ts": None},
        }

    def metrics_payload(self) -> Dict[str, Any]:
        # Get queue size from active trade stream
        if self.bybit_trade_stream:
            trade_queue_size = self.bybit_trade_stream.queue_size
        elif self.trade_stream:
            trade_queue_size = self.trade_stream.queue_size
        else:
            trade_queue_size = 0
            
        snapshot: MetricsSnapshot = self.metrics.snapshot(
            trade_queue_size=trade_queue_size,
            depth_queue_size=self.depth_stream.queue_size if self.depth_stream else 0,
        )
        return snapshot.model_dump()

    @staticmethod
    def _serialize_health(health: StreamHealth) -> Dict[str, Any]:
        return health.model_dump()

    def get_recent_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trades from active trade stream."""
        if self.bybit_trade_stream:
            return self.bybit_trade_stream.get_recent_trades(limit)
        elif self.trade_stream:
            # For Binance, we would need to implement similar functionality
            return []
        else:
            return []


@lru_cache(maxsize=1)
def get_ws_module() -> WSModule:
    return WSModule()


@router.get("/health")
async def ws_health() -> Dict[str, Any]:
    module = get_ws_module()
    return module.health_payload()


@router.get("/metrics")
async def metrics() -> Dict[str, Any]:
    module = get_ws_module()
    return module.metrics_payload()


@router.get("/trades")
async def get_ws_trades(limit: int = 100) -> Dict[str, Any]:
    """Get recent trades from active WebSocket stream."""
    module = get_ws_module()
    trades = module.get_recent_trades(limit)
    
    return {
        "trades": trades,
        "count": len(trades),
        "data_source": module.settings.data_source,
    }