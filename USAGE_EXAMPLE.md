# Bybit WebSocket Connector - Usage Example

## Quick Start

### 1. Configure Environment

Create or update your `.env` file:

```bash
# Enable Bybit WebSocket
DATA_SOURCE=bybit_ws

# Bybit Configuration
BYBIT_SYMBOL=BTCUSDT
WEBSOCKET_BUFFER_SIZE=1000
BYBIT_CONNECTOR_TESTNET=false  # Set to true for testnet

# Other settings
LOG_LEVEL=INFO
```

### 2. Start the Backend

```bash
cd backend
source ../.venv/bin/activate  # or your venv
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Test the API

```bash
# Check health and WebSocket status
curl http://localhost:8000/health | jq

# Get recent trades
curl http://localhost:8000/trades?limit=10 | jq '.trades[0:5]'

# Get trade statistics
curl http://localhost:8000/trades/stats | jq

# Get WebSocket-specific trades
curl http://localhost:8000/ws/trades?limit=5 | jq

# Get detailed WebSocket health
curl http://localhost:8000/ws/health | jq
```

### 4. Expected Output

#### Health Check
```json
{
  "status": "Healthy",
  "websocket_connected": true
}
```

#### Recent Trades
```json
{
  "trades": [
    {
      "price": 43250.5,
      "qty": 0.1,
      "side": "Buy",
      "time": "2024-01-01T12:00:00.000Z",
      "symbol": "BTCUSDT",
      "trade_id": "123456789"
    }
  ],
  "count": 1,
  "data_source": "bybit_ws"
}
```

#### Trade Statistics
```json
{
  "total_count": 1000,
  "oldest_trade_time": "2024-01-01T11:55:00.000Z",
  "newest_trade_time": "2024-01-01T12:00:00.000Z",
  "bybit_connected": true
}
```

## Advanced Usage

### Python Client Example

```python
import asyncio
import aiohttp
import json

async def monitor_trades():
    """Monitor live trades from the API."""
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # Get latest trades
                async with session.get("http://localhost:8000/trades?limit=5") as resp:
                    data = await resp.json()
                    
                if data["trades"]:
                    latest = data["trades"][0]
                    print(f"{latest['time']}: {latest['side']} {latest['qty']} @ {latest['price']}")
                
                # Check connection status
                async with session.get("http://localhost:8000/health") as resp:
                    health = await resp.json()
                    if not health.get("websocket_connected"):
                        print("⚠️  WebSocket disconnected!")
                
                await asyncio.sleep(5)  # Poll every 5 seconds
                
            except Exception as e:
                print(f"Error: {e}")
                await asyncio.sleep(10)

# Run the monitor
asyncio.run(monitor_trades())
```

### Direct WebSocket Usage

```python
import asyncio
from app.connectors.bybit_websocket import BybitWebSocketConnector

async def direct_websocket_example():
    """Example of using the WebSocket connector directly."""
    
    connector = BybitWebSocketConnector(
        symbol="BTCUSDT",
        buffer_size=100,
        testnet=True
    )
    
    try:
        await connector.connect()
        print("Connected to Bybit WebSocket!")
        
        # Monitor for 30 seconds
        for i in range(30):
            await asyncio.sleep(1)
            trades = connector.get_recent_trades(3)
            if trades:
                print(f"Latest {len(trades)} trades:")
                for trade in trades:
                    print(f"  {trade['time']}: {trade['side']} {trade['qty']} @ {trade['price']}")
            else:
                print("No trades received yet...")
                
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await connector.disconnect()
        print("Disconnected")

asyncio.run(direct_websocket_example())
```

## Switching Data Sources

You can easily switch between Binance and Bybit:

```bash
# Use Binance (default)
DATA_SOURCE=binance_ws

# Use Bybit
DATA_SOURCE=bybit_ws
```

The system will automatically use the appropriate WebSocket connector based on the `DATA_SOURCE` setting.

## Monitoring and Debugging

### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG
python -m uvicorn app.main:app --reload
```

### Monitor Connection Status

```bash
# Watch WebSocket health
watch -n 5 'curl -s http://localhost:8000/ws/health | jq'

# Watch trade statistics
watch -n 10 'curl -s http://localhost:8000/trades/stats | jq'
```

### Check Logs

The connector provides structured logs for:

- Connection events
- Trade reception
- Errors and reconnections
- Performance metrics

Example log output:
```json
{"timestamp": "2024-01-01T12:00:00.000Z", "event": "bybit_ws_connected", "symbol": "BTCUSDT", "testnet": false}
{"timestamp": "2024-01-01T12:00:01.000Z", "event": "bybit_trade", "price": 43250.5, "qty": 0.1, "side": "Buy", "lag_ms": 15.2, "buffer_size": 100}
```

## Troubleshooting

### No Trades Received

1. Check if the market is active
2. Verify symbol is correct (e.g., BTCUSDT)
3. Check network connectivity
4. Review logs for connection issues

### Connection Issues

1. Check testnet vs mainnet settings
2. Verify firewall allows WebSocket connections
3. Check rate limiting
4. Review error logs

### Performance Issues

1. Reduce `WEBSOCKET_BUFFER_SIZE` if memory usage is high
2. Monitor trade frequency for your symbol
3. Check for network latency

## Production Deployment

For production use:

1. Set `BYBIT_CONNECTOR_TESTNET=false`
2. Use appropriate buffer size based on trade volume
3. Monitor memory usage and performance
4. Set up proper logging and monitoring
5. Consider using a process manager like systemd

```bash
# Production environment
DATA_SOURCE=bybit_ws
BYBIT_SYMBOL=BTCUSDT
WEBSOCKET_BUFFER_SIZE=5000
BYBIT_CONNECTOR_TESTNET=false
LOG_LEVEL=INFO
```