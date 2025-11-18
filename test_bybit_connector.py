#!/usr/bin/env python3
"""
Test script for Bybit WebSocket connector.
Run this to verify the implementation works correctly.
"""

import asyncio
import os
import sys
from datetime import datetime

# Add the backend directory to Python path
sys.path.insert(0, '/home/engine/project/backend')

from app.connectors.bybit_websocket import BybitWebSocketConnector
from app.ws.models import get_settings


async def test_bybit_connector():
    """Test the Bybit WebSocket connector."""
    print("Testing Bybit WebSocket connector...")
    
    # Get settings
    settings = get_settings()
    
    # Create connector
    connector = BybitWebSocketConnector(
        symbol=settings.symbol,
        buffer_size=100,
        testnet=True,  # Use testnet for testing
    )
    
    try:
        print(f"Connecting to Bybit WebSocket for {settings.symbol}...")
        await connector.connect()
        
        # Wait for some trades
        print("Waiting for trades (30 seconds)...")
        await asyncio.sleep(30)
        
        # Get recent trades
        trades = connector.get_recent_trades(10)
        print(f"Received {len(trades)} trades")
        
        if trades:
            print("Latest trades:")
            for i, trade in enumerate(trades[:5]):
                print(f"  {i+1}. {trade['time']}: {trade['side']} {trade['qty']} @ {trade['price']}")
        
        # Get stats
        print(f"Connection status: {connector.is_connected}")
        print(f"Total trades in buffer: {connector.trade_count}")
        print(f"Last trade time: {connector.last_trade_time}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await connector.disconnect()
        print("Disconnected")


async def test_trade_service():
    """Test the trade service."""
    print("\nTesting Trade Service...")
    
    from app.services.trade_service import TradeService
    from app.ws.models import get_settings
    
    settings = get_settings()
    service = TradeService(settings)
    
    try:
        print("Starting Bybit connector...")
        await service.start_bybit_connector()
        
        # Wait for some trades
        await asyncio.sleep(20)
        
        # Get recent trades
        trades = service.get_recent_trades(5)
        print(f"Service has {len(trades)} recent trades")
        
        # Get stats
        stats = service.get_stats()
        print(f"Stats: {stats}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await service.stop_bybit_connector()


async def test_api_endpoints():
    """Test the API endpoints (requires server running)."""
    print("\nTo test API endpoints:")
    print("1. Start the server: cd backend && python -m uvicorn app.main:app --reload")
    print("2. Set DATA_SOURCE=bybit_ws in .env")
    print("3. Test with curl:")
    print("   curl http://localhost:8000/health | jq")
    print("   curl http://localhost:8000/trades | jq '.trades[0:5]'")
    print("   curl http://localhost:8000/trades/stats | jq")
    print("   curl http://localhost:8000/ws/trades | jq")


if __name__ == "__main__":
    print("Bybit WebSocket Connector Test")
    print("=" * 40)
    
    # Check environment
    if os.getenv("DATA_SOURCE") != "bybit_ws":
        print("Note: Set DATA_SOURCE=bybit_ws to test with Bybit")
    
    # Run tests
    asyncio.run(test_bybit_connector())
    asyncio.run(test_trade_service())
    asyncio.run(test_api_endpoints())
    
    print("\nTest completed!")