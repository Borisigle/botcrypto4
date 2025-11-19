"""Binance Futures WebSocket connector for real-time liquidation events."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

import websockets
from websockets import WebSocketException


class LiquidationWebSocketConnector:
    """WebSocket connector for Binance Futures liquidation (forceOrder) events."""
    
    def __init__(
        self,
        symbol: str = "btcusdt",
        on_liquidation: Optional[Callable] = None,
    ):
        """Initialize liquidation WebSocket connector.
        
        Args:
            symbol: Trading pair symbol (e.g., btcusdt, ethusdt)
            on_liquidation: Async callback function called when liquidation received
        """
        self.symbol = symbol.lower()
        self.on_liquidation = on_liquidation
        self._websocket: Optional[websockets.WebSocketServerProtocol] = None
        self._connected = False
        self._stop_event = asyncio.Event()
        
        # WebSocket URL for force orders (liquidations)
        # Using @arr suffix for array format (returns array of liquidations)
        self.url = f"wss://fstream.binance.com/ws/{self.symbol}@forceOrder@arr"
        
        self.logger = logging.getLogger("liquidation_ws")
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is currently connected."""
        return self._connected
    
    async def connect(self) -> None:
        """Connect to Binance liquidation WebSocket and start listening."""
        if self._connected:
            self.logger.warning("WebSocket already connected")
            return
        
        self._stop_event.clear()
        await self._reconnect_loop()
    
    async def close(self) -> None:
        """Close WebSocket connection and cleanup."""
        self._stop_event.set()
        
        if self._websocket:
            await self._websocket.close()
            self._websocket = None
        
        self._connected = False
        self.logger.info("Liquidation WebSocket closed")
    
    async def _reconnect_loop(self) -> None:
        """Main reconnection loop with exponential backoff."""
        backoff = 1.0
        max_backoff = 30.0
        
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
                backoff = 1.0  # Reset backoff on successful connection
            except asyncio.CancelledError:
                self.logger.info("Liquidation WebSocket reconnect loop cancelled")
                break
            except (OSError, WebSocketException) as exc:
                self.logger.error(
                    "Liquidation WebSocket error: %s (reconnect in %.1fs)",
                    exc,
                    backoff,
                )
            except Exception as exc:
                self.logger.exception("Unexpected liquidation WebSocket error: %s", exc)
            finally:
                self._connected = False
                self._websocket = None
            
            if self._stop_event.is_set():
                break
            
            # Sleep with exponential backoff
            sleep_for = min(backoff, max_backoff)
            await asyncio.sleep(sleep_for)
            backoff = min(backoff * 2, max_backoff)
    
    async def _connect_and_listen(self) -> None:
        """Establish WebSocket connection and listen for liquidation events."""
        self.logger.info("Connecting to Binance liquidation WebSocket: %s", self.url)
        
        self._websocket = await websockets.connect(
            self.url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        )
        
        self._connected = True
        self.logger.info("Liquidation WebSocket connected: %s", self.url)
        
        # Listen for incoming messages
        await self._listen()
    
    async def _listen(self) -> None:
        """Listen for incoming WebSocket messages."""
        async for message in self._websocket:
            if self._stop_event.is_set():
                break
            
            try:
                data = json.loads(message)
                payloads = data if isinstance(data, list) else [data]
                for payload in payloads:
                    await self._handle_message(payload)
            except json.JSONDecodeError as exc:
                self.logger.warning("Failed to parse liquidation message: %s", exc)
                continue
            except Exception as exc:
                self.logger.error("Error handling liquidation message: %s", exc)
                continue
    
    async def _handle_message(self, data: Dict[str, Any]) -> None:
        """Handle incoming liquidation message.
        
        Expected format:
        {
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "IOC",
                "q": "0.014",
                "p": "91500.50",
                "ap": "91501.17",
                "X": "FILLED",
                "l": "0.014",
                "z": "0.014",
                "T": 1638747660000
            }
        }
        """
        if "o" not in data:
            self.logger.debug("Received non-liquidation message: %s", data)
            return
        
        order = data["o"]
        
        try:
            # Parse liquidation order
            liquidation = {
                "price": float(order.get("p", 0)),
                "qty": float(order.get("q", 0)),
                "side": "buy" if order.get("S", "").upper() == "BUY" else "sell",
                "time": datetime.fromtimestamp(
                    int(order.get("T", 0)) / 1000,
                    tz=timezone.utc
                ),
                "symbol": order.get("s", self.symbol.upper()),
                "avg_price": float(order.get("ap", 0)),
                "status": order.get("X", ""),
            }
            
            # Calculate lag
            lag_ms = (datetime.now(timezone.utc) - liquidation["time"]).total_seconds() * 1000
            
            self.logger.debug(
                "Liquidation event: price=%.2f, qty=%.4f, side=%s, lag_ms=%.1f",
                liquidation["price"],
                liquidation["qty"],
                liquidation["side"],
                lag_ms,
            )
            
            # Call the callback if provided
            if self.on_liquidation:
                await self.on_liquidation(liquidation)
        
        except (KeyError, ValueError, TypeError) as exc:
            self.logger.warning("Failed to parse liquidation order: %s, data=%s", exc, order)


async def main():
    """Test the liquidation WebSocket connector."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    async def on_liq(liq: dict):
        print(f"[LIQUIDATION] {liq['side'].upper():<4} {liq['qty']:>8.4f} @ ${liq['price']:,.2f}")
    
    connector = LiquidationWebSocketConnector(
        symbol="btcusdt",
        on_liquidation=on_liq
    )
    
    try:
        await connector.connect()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await connector.close()


if __name__ == "__main__":
    asyncio.run(main())
