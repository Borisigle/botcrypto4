"""Trade service for managing trade data from multiple sources."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..connectors.bybit_websocket import BybitWebSocketConnector
from ..ws.models import Settings


class TradeService:
    """Service for managing trade data from WebSocket connectors."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._buffer_size = settings.max_queue
        self._trades_buffer: deque[Dict[str, Any]] = deque(maxlen=self._buffer_size)
        self._bybit_connector: Optional[BybitWebSocketConnector] = None
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger("trade_service")
        
    async def start_bybit_connector(self) -> None:
        """Start Bybit WebSocket connector."""
        if self._bybit_connector is None:
            self._bybit_connector = BybitWebSocketConnector(
                symbol=self.settings.symbol,
                buffer_size=self._buffer_size,
                testnet=self.settings.bybit_connector_testnet,
            )
            await self._bybit_connector.connect()
            
    async def stop_bybit_connector(self) -> None:
        """Stop Bybit WebSocket connector."""
        if self._bybit_connector:
            await self._bybit_connector.disconnect()
            self._bybit_connector = None
            
    async def add_trade(self, trade_data: Dict[str, Any]) -> None:
        """Add a trade to the buffer."""
        async with self._lock:
            self._trades_buffer.append(trade_data)
            self.logger.info(
                f"Trade added: price={trade_data.get('price')}, "
                f"qty={trade_data.get('qty')}, side={trade_data.get('side')}, "
                f"buffer_size={len(self._trades_buffer)}"
            )
            
    def get_recent_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get most recent trades from buffer."""
        trades = list(self._trades_buffer)
        trades.sort(key=lambda t: t["time"], reverse=True)
        return trades[:limit]
        
    def get_trades_range(
        self, 
        start_time: datetime, 
        end_time: datetime
    ) -> List[Dict[str, Any]]:
        """Get trades within time range."""
        trades = [
            trade 
            for trade in self._trades_buffer 
            if start_time <= datetime.fromisoformat(trade["time"]) <= end_time
        ]
        trades.sort(key=lambda t: t["time"], reverse=True)
        return trades
        
    def get_stats(self) -> Dict[str, Any]:
        """Get trade statistics."""
        if not self._trades_buffer:
            return {
                "total_count": 0,
                "oldest_trade_time": None,
                "newest_trade_time": None,
                "buffer_size": self._buffer_size,
            }
            
        trades_list = list(self._trades_buffer)
        trades_list.sort(key=lambda t: t["time"])
        
        return {
            "total_count": len(trades_list),
            "oldest_trade_time": trades_list[0]["time"],
            "newest_trade_time": trades_list[-1]["time"],
            "buffer_size": self._buffer_size,
        }
        
    @property
    def is_bybit_connected(self) -> bool:
        """Check if Bybit connector is connected."""
        return self._bybit_connector.is_connected if self._bybit_connector else False