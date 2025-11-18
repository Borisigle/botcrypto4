"""Bybit WebSocket connector for live trade streaming."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import websockets
from websockets import WebSocketException

from ..ws.client import BaseStreamService, structured_log
from ..ws.metrics import MetricsRecorder
from ..ws.models import Settings, StreamHealth, TradeSide, TradeTick


class BybitTrade:
    """Trade model for Bybit WebSocket data."""
    
    def __init__(
        self,
        price: float,
        qty: float,
        side: str,
        time: datetime,
        symbol: str,
        trade_id: str,
    ):
        self.price = price
        self.qty = qty
        self.side = side  # "Buy" or "Sell"
        self.time = time
        self.symbol = symbol
        self.trade_id = trade_id

    def to_dict(self) -> Dict[str, Any]:
        """Convert trade to dictionary for JSON serialization."""
        return {
            "price": self.price,
            "qty": self.qty,
            "side": self.side,
            "time": self.time.isoformat(),
            "symbol": self.symbol,
            "trade_id": self.trade_id,
        }
        
    def to_trade_tick(self) -> TradeTick:
        """Convert to TradeTick format for strategy engine compatibility."""
        # Convert Bybit side to TradeSide enum
        trade_side = TradeSide.BUY if self.side == "Buy" else TradeSide.SELL
        # Bybit doesn't provide isBuyerMaker, so we'll infer it
        # In Bybit: "Buy" means taker was buyer, "Sell" means taker was seller
        is_buyer_maker = self.side == "Sell"
        
        return TradeTick(
            ts=self.time,
            price=self.price,
            qty=self.qty,
            side=trade_side,
            isBuyerMaker=is_buyer_maker,
            id=int(self.trade_id) if self.trade_id.isdigit() else 0,
        )


class BybitWebSocketConnector:
    """WebSocket connector for Bybit live trade streaming."""
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        buffer_size: int = 1000,
        testnet: bool = False,
    ):
        self.symbol = symbol.upper()
        self.buffer_size = buffer_size
        self.testnet = testnet
        
        # WebSocket URL based on testnet setting
        if testnet:
            self.ws_url = "wss://stream.bybit.com/v5/public/spot"
        else:
            self.ws_url = "wss://stream.bybit.com/v5/public/linear"  # Futures by default
        
        self._trades_buffer: deque[BybitTrade] = deque(maxlen=buffer_size)
        self._connected = False
        self._last_trade_time: Optional[datetime] = None
        self._websocket: Optional[websockets.WebSocketServerProtocol] = None
        self._stop_event = asyncio.Event()
        self._reconnect_task: Optional[asyncio.Task[None]] = None
        self.logger = logging.getLogger("bybit_ws")
        
    async def connect(self) -> None:
        """Connect to Bybit WebSocket and subscribe to trades."""
        if self._connected:
            return
            
        self._stop_event.clear()
        await self._reconnect_loop()
        
    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._stop_event.set()
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
                
        if self._websocket:
            await self._websocket.close()
        self._connected = False
        
    async def _reconnect_loop(self) -> None:
        """Main reconnection loop with exponential backoff."""
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                await self._connect_and_subscribe()
                backoff = 1.0  # Reset backoff on successful connection
                await self._listen_loop()
            except asyncio.CancelledError:
                break
            except (OSError, WebSocketException) as exc:
                structured_log(
                    self.logger,
                    "bybit_ws_error",
                    error=str(exc),
                    reconnect_delay=round(backoff, 2),
                )
            finally:
                self._connected = False
                self._websocket = None
                
            if self._stop_event.is_set():
                break
                
            sleep_for = min(backoff, 10.0)
            await asyncio.sleep(sleep_for)
            backoff = min(backoff * 2, 30.0)
            
    async def _connect_and_subscribe(self) -> None:
        """Establish WebSocket connection and subscribe to trades."""
        self._websocket = await websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        )
        
        # Subscribe to trades
        subscribe_msg = {
            "op": "subscribe",
            "args": [f"publicTrade.{self.symbol}"]
        }
        
        await self._websocket.send(json.dumps(subscribe_msg))
        
        # Wait for subscription confirmation
        response = await self._websocket.recv()
        data = json.loads(response)
        
        if data.get("success") is True:
            self._connected = True
            structured_log(
                self.logger,
                "bybit_ws_connected",
                symbol=self.symbol,
                testnet=self.testnet,
                url=self.ws_url,
            )
        else:
            raise Exception(f"Subscription failed: {data}")
            
    async def _listen_loop(self) -> None:
        """Listen for incoming WebSocket messages."""
        async for message in self._websocket:
            if self._stop_event.is_set():
                break
                
            try:
                data = json.loads(message)
                await self._handle_message(data)
            except json.JSONDecodeError as exc:
                structured_log(
                    self.logger,
                    "bybit_decode_error",
                    error=str(exc),
                )
                continue
                
    async def _handle_message(self, data: Dict[str, Any]) -> None:
        """Handle incoming WebSocket message."""
        # Handle subscription confirmation
        if "success" in data:
            if data.get("success"):
                structured_log(
                    self.logger,
                    "bybit_subscription_confirmed",
                    topic=data.get("topic"),
                )
            else:
                structured_log(
                    self.logger,
                    "bybit_subscription_failed",
                    error=data.get("ret_msg"),
                )
            return
            
        # Handle trade data
        if "topic" in data and "data" in data:
            topic = data["topic"]
            if topic == f"publicTrade.{self.symbol}":
                await self._process_trades(data["data"])
                
    async def _process_trades(self, trades_data: list[Dict[str, Any]]) -> None:
        """Process incoming trade data."""
        for trade_data in trades_data:
            try:
                trade = BybitTrade(
                    price=float(trade_data["p"]),
                    qty=float(trade_data["v"]),
                    side=trade_data["S"],  # "Buy" or "Sell"
                    time=datetime.fromtimestamp(int(trade_data["T"]) / 1000, tz=timezone.utc),
                    symbol=self.symbol,
                    trade_id=trade_data["i"],
                )
                
                self._trades_buffer.append(trade)
                self._last_trade_time = trade.time
                
                lag_ms = (datetime.now(timezone.utc) - trade.time).total_seconds() * 1000
                structured_log(
                    self.logger,
                    "bybit_trade",
                    price=trade.price,
                    qty=trade.qty,
                    side=trade.side,
                    trade_id=trade.trade_id,
                    lag_ms=round(lag_ms, 2),
                    buffer_size=len(self._trades_buffer),
                )
                
            except (KeyError, ValueError) as exc:
                structured_log(
                    self.logger,
                    "bybit_trade_parse_error",
                    error=str(exc),
                    trade_data=trade_data,
                )
                
    def get_recent_trades(self, limit: int = 100) -> list[Dict[str, Any]]:
        """Get most recent trades from buffer."""
        trades = list(self._trades_buffer)
        trades.sort(key=lambda t: t.time, reverse=True)
        return [trade.to_dict() for trade in trades[:limit]]
        
    def get_trades_range(
        self, 
        start_time: datetime, 
        end_time: datetime
    ) -> list[Dict[str, Any]]:
        """Get trades within time range."""
        trades = [
            trade.to_dict() 
            for trade in self._trades_buffer 
            if start_time <= trade.time <= end_time
        ]
        trades.sort(key=lambda t: t["time"], reverse=True)
        return trades
        
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected
        
    @property
    def last_trade_time(self) -> Optional[datetime]:
        """Get timestamp of last received trade."""
        return self._last_trade_time
        
    @property
    def trade_count(self) -> int:
        """Get number of trades in buffer."""
        return len(self._trades_buffer)


class BybitWebSocketStream(BaseStreamService):
    """Bybit WebSocket stream integrated with existing architecture."""
    
    def __init__(
        self,
        settings: Settings,
        metrics: MetricsRecorder,
    ) -> None:
        # Use a placeholder URL since we'll override the connection logic
        super().__init__("bybit_trades", "", settings)
        self.metrics = metrics
        self._connector: Optional[BybitWebSocketConnector] = None
        self._strategy_engine = None
        
    def set_strategy_engine(self, strategy_engine) -> None:
        """Set the strategy engine reference for trade forwarding."""
        self._strategy_engine = strategy_engine
        
    async def on_start(self) -> None:
        """Initialize Bybit WebSocket connector."""
        self._connector = BybitWebSocketConnector(
            symbol=self.settings.symbol,
            buffer_size=self.settings.max_queue,
            testnet=self.settings.bybit_connector_testnet,
        )
        await self._connector.connect()
        
    async def on_stop(self) -> None:
        """Cleanup Bybit WebSocket connector."""
        if self._connector:
            await self._connector.disconnect()
            self._connector = None
            
    async def handle_payload(self, payload: Any) -> None:
        """Handle payload - not used as we override the connection logic."""
        pass
        
    def health(self) -> StreamHealth:
        """Get stream health status."""
        if self._connector:
            self.state.connected = self._connector.is_connected
            self.state.last_ts = self._connector.last_trade_time
        return self.state.snapshot()
        
    @property
    def queue_size(self) -> int:
        """Get queue size (trades buffer)."""
        return self._connector.trade_count if self._connector else 0
        
    def get_recent_trades(self, limit: int = 100) -> list[Dict[str, Any]]:
        """Get recent trades from connector."""
        if self._connector:
            trades = self._connector.get_recent_trades(limit)
            # Forward trades to strategy engine if available
            if self._strategy_engine:
                for trade_dict in trades:
                    # Convert back to BybitTrade object for conversion
                    trade = BybitTrade(
                        price=trade_dict["price"],
                        qty=trade_dict["qty"],
                        side=trade_dict["side"],
                        time=datetime.fromisoformat(trade_dict["time"]),
                        symbol=trade_dict["symbol"],
                        trade_id=trade_dict["trade_id"],
                    )
                    trade_tick = trade.to_trade_tick()
                    self._strategy_engine.ingest_trade(trade_tick)
            return trades
        return []