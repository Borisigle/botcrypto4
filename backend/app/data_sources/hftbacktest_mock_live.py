"""Mock implementation of hftbacktest.live for development/testing.

This provides the same interface as hftbacktest.live but uses websockets
instead of the native Rust implementation.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timezone
import websockets
import aiohttp


class MockLiveClient:
    """Mock implementation of hftbacktest.live client."""
    
    def __init__(self, exchange: str, config: Dict[str, Any], paper_trading: bool = False):
        self.exchange = exchange
        self.config = config
        self.paper_trading = paper_trading
        self.logger = logging.getLogger(f"mock_live_client_{exchange}")
        self._subscriptions: List[Dict[str, Any]] = []
        self._websocket: Optional[websockets.WebSocketServerProtocol] = None
        self._event_queue = asyncio.Queue()
        self._running = False
        
    async def subscribe(self, subscriptions: List[Dict[str, Any]]) -> None:
        """Subscribe to channels."""
        self._subscriptions = subscriptions
        self.logger.info(f"Subscribed to {len(subscriptions)} channels: {subscriptions}")
        
    async def next_event(self) -> Optional[Any]:
        """Get the next event."""
        if not self._running:
            return None
            
        try:
            # Wait for an event with timeout
            event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
            return event
        except asyncio.TimeoutError:
            return None
            
    async def close(self) -> None:
        """Close the connection."""
        self._running = False
        if self._websocket:
            await self._websocket.close()
            self._websocket = None
        self.logger.info("Mock live client closed")
        
    async def _simulate_events(self) -> None:
        """Simulate market events for testing."""
        symbol = self.config.get('symbol', 'BTCUSDT')
        
        while self._running:
            # Simulate a trade event
            trade_event = MockEvent(
                type='trade',
                timestamp=datetime.now(timezone.utc).timestamp(),
                price=50000.0 + (hash(str(datetime.now())) % 1000),
                qty=0.1 + (hash(str(datetime.now())) % 100) / 1000,
                side=1 if hash(str(datetime.now())) % 2 == 0 else 0,
                is_buyer_maker=hash(str(datetime.now())) % 2 == 0,
                id=abs(hash(str(datetime.now())) % 1000000)
            )
            await self._event_queue.put(trade_event)
            
            # Simulate a depth event
            depth_event = MockEvent(
                type='depth',
                timestamp=datetime.now(timezone.utc).timestamp(),
                bids=[[50000.0 - i*0.5, 0.1 + i*0.01] for i in range(5)],
                asks=[[50001.0 + i*0.5, 0.1 + i*0.01] for i in range(5)],
                last_update_id=abs(hash(str(datetime.now())) % 1000000)
            )
            await self._event_queue.put(depth_event)
            
            # Wait before next event
            await asyncio.sleep(0.1)


class MockEvent:
    """Mock event object that mimics hftbacktest event structure."""
    
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


async def create(exchange: str, config: Dict[str, Any], paper_trading: bool = False) -> MockLiveClient:
    """Create a mock live client."""
    client = MockLiveClient(exchange, config, paper_trading)
    client._running = True
    # Start event simulation in background
    asyncio.create_task(client._simulate_events())
    return client


async def run_live(client: MockLiveClient) -> None:
    """Run the live client (mock implementation)."""
    try:
        client._running = True
        while client._running:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        client._running = False
        await client.close()