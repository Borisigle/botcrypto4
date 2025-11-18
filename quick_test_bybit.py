#!/usr/bin/env python3
"""
Quick test of Bybit WebSocket connection.
"""

import asyncio
import sys
import os

# Add the backend directory to Python path
sys.path.insert(0, '/home/engine/project/backend')

from app.connectors.bybit_websocket import BybitWebSocketConnector


async def quick_connection_test():
    """Quick test of Bybit WebSocket connection."""
    print("Testing Bybit WebSocket connection (testnet)...")
    
    # Create connector with testnet
    connector = BybitWebSocketConnector(
        symbol="BTCUSDT",
        buffer_size=10,
        testnet=True,  # Use testnet
    )
    
    try:
        print("Connecting to Bybit...")
        await connector.connect()
        
        # Wait a few seconds for connection and subscription
        print("Waiting for connection confirmation...")
        await asyncio.sleep(5)
        
        # Check connection status
        print(f"Connected: {connector.is_connected}")
        print(f"Trades received: {connector.trade_count}")
        
        if connector.is_connected:
            print("✓ Successfully connected to Bybit WebSocket!")
        else:
            print("✗ Failed to connect to Bybit WebSocket")
        
    except Exception as e:
        print(f"✗ Error: {e}")
    finally:
        await connector.disconnect()
        print("Disconnected")


if __name__ == "__main__":
    asyncio.run(quick_connection_test())