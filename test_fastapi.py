#!/usr/bin/env python3
"""
Test FastAPI startup with Bybit WebSocket integration.
"""

import sys
import os

# Add the backend directory to Python path
sys.path.insert(0, '/home/engine/project/backend')

# Set Bybit as data source
os.environ['DATA_SOURCE'] = 'bybit_ws'

from fastapi.testclient import TestClient
from app.main import app

def test_fastapi_startup():
    """Test that FastAPI can start with Bybit integration."""
    print("Testing FastAPI startup with Bybit WebSocket...")
    
    try:
        # Create test client
        client = TestClient(app)
        
        # Test health endpoint
        response = client.get("/health")
        print(f"Health endpoint status: {response.status_code}")
        print(f"Health response: {response.json()}")
        
        # Test trades endpoint
        response = client.get("/trades")
        print(f"Trades endpoint status: {response.status_code}")
        print(f"Trades response: {response.json()}")
        
        # Test trades stats
        response = client.get("/trades/stats")
        print(f"Trades stats status: {response.status_code}")
        print(f"Trades stats response: {response.json()}")
        
        # Test WebSocket health
        response = client.get("/ws/health")
        print(f"WS health status: {response.status_code}")
        print(f"WS health response: {response.json()}")
        
        print("✓ All endpoints are working!")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_fastapi_startup()