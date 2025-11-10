# HFT Connector Feed Guide

## Overview

The HFT Connector Feed is a pluggable data source adapter that allows the trading system to ingest market data from various exchange connectors, including the `hftbacktest` live wrapper. This enables trading strategies to run against different data sources while maintaining a unified data ingestion pipeline.

## Architecture

### Components

1. **ConnectorWrapper** (Abstract Base Class)
   - Defines the interface for exchange connector implementations
   - Methods: `connect()`, `disconnect()`, `subscribe_trades()`, `subscribe_depth()`, `next_event()`, `is_connected()`, `get_health_status()`

2. **HFTConnectorStream** (Adapter)
   - Wraps a ConnectorWrapper implementation
   - Converts connector events to standard `TradeTick` and `DepthUpdate` models
   - Inherits from `BaseStreamService` for integration with existing pipeline
   - Handles reconnection logic with exponential backoff
   - Forwards events to context service and strategy engine

3. **StubbedConnector** (Testing Implementation)
   - Simulates a real connector for development and testing
   - Generates synthetic trade and depth events
   - Useful for testing without a live exchange connection

### Data Flow

```
Connector (hftbacktest or stubbed)
    ↓
HFTConnectorStream.next_event()
    ↓
Event Parsing (_parse_connector_trade, _parse_connector_depth)
    ↓
TradeTick / DepthUpdate
    ↓
Context Service + Strategy Engine
```

## Configuration

### Environment Variables

Add to `.env` or set in environment:

```bash
# Data source selection (default: binance_ws)
DATA_SOURCE=hft_connector

# Connector-specific settings (used when DATA_SOURCE=hft_connector)
CONNECTOR_NAME=binance_hft
CONNECTOR_POLL_INTERVAL_MS=100
CONNECTOR_PAPER_TRADING=true
```

### Settings Dataclass

Configuration is loaded via the `Settings` dataclass in `app/ws/models.py`:

```python
from app.ws.models import get_settings

settings = get_settings()
print(settings.data_source)  # "binance_ws" or "hft_connector"
print(settings.connector_name)  # Optional connector name
print(settings.connector_poll_interval_ms)  # Poll interval
print(settings.connector_paper_trading)  # Paper trading mode
```

## Usage

### Using the Default Binance WebSocket (Default)

```python
from app.ws.models import Settings

# Default configuration uses native Binance WebSocket
settings = Settings()
assert settings.data_source == "binance_ws"
```

### Using the HFT Connector

#### 1. Set Environment Variable

```bash
export DATA_SOURCE=hft_connector
export CONNECTOR_NAME=binance_hft
```

#### 2. Create a Connector Implementation

Implement the `ConnectorWrapper` interface:

```python
from app.data_sources.hft_connector import ConnectorWrapper
from typing import Optional, Any
from datetime import datetime, timezone

class MyCustomConnector(ConnectorWrapper):
    async def connect(self) -> None:
        """Connect to the exchange."""
        # Implementation here
        pass

    async def disconnect(self) -> None:
        """Disconnect from the exchange."""
        pass

    async def subscribe_trades(self, symbol: str) -> None:
        """Subscribe to trades."""
        pass

    async def subscribe_depth(self, symbol: str) -> None:
        """Subscribe to depth."""
        pass

    async def next_event(self) -> Optional[dict[str, Any]]:
        """Get next event."""
        # Must return dict with 'type' key: 'trade' or 'depth'
        # For trades:
        return {
            'type': 'trade',
            'timestamp': datetime.now(timezone.utc),
            'price': 100.5,
            'qty': 0.5,
            'side': 'buy',
            'is_buyer_maker': False,
            'id': 12345
        }
        
        # For depth:
        return {
            'type': 'depth',
            'timestamp': datetime.now(timezone.utc),
            'bids': [(100.0, 1.0), (99.9, 2.0)],
            'asks': [(100.1, 1.0), (100.2, 2.0)],
            'last_update_id': 42
        }

    async def is_connected(self) -> bool:
        """Check if connected."""
        pass

    def get_health_status(self) -> dict[str, Any]:
        """Get connector health."""
        return {
            'connected': True,
            'symbol': 'BTCUSDT'
        }
```

#### 3. Update WSModule Initialization

In `app/ws/routes.py`, the `_setup_connector_mode()` method handles initialization. By default, it uses the `StubbedConnector` for testing:

```python
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
    # ...
```

To use a real connector, replace the connector instantiation:

```python
async def _setup_connector_mode(self) -> None:
    """Setup connector mode."""
    from my_connectors import MyCustomConnector
    
    connector = MyCustomConnector(self.settings)
    # ... rest of initialization
```

### Event Format

Trade events must include:
- `type`: "trade"
- `timestamp`: datetime or int (milliseconds since epoch)
- `price`: float
- `qty`: float
- `side`: "buy" or "sell"
- `is_buyer_maker`: bool
- `id`: int (unique trade ID)

Depth events must include:
- `type`: "depth"
- `timestamp`: datetime or int (milliseconds since epoch)
- `bids`: list of [price, qty] tuples
- `asks`: list of [price, qty] tuples
- `last_update_id`: int

## Monitoring

### Health Status

Check connector health via the API:

```bash
curl http://localhost:8000/ws/health
```

Response when using HFT Connector:
```json
{
  "connector": {
    "connected": true,
    "last_ts": "2024-01-15T12:34:56.789123+00:00",
    "queue_size": 0,
    "reconnection_attempts": 0,
    "connector_health": {
      "connected": true,
      "subscribed_trades": true,
      "subscribed_depth": true,
      "event_counter": 1234
    }
  }
}
```

### Metrics

Check metrics via the API:

```bash
curl http://localhost:8000/metrics
```

Response:
```json
{
  "trades": {
    "per_minute_count": 240,
    "per_second_rate": 4.0,
    "queue_size": 0
  },
  "depth": {
    "per_minute_count": 60,
    "per_second_rate": 1.0,
    "queue_size": 0
  }
}
```

### Structured Logging

The connector logs structured events to stdout:

```json
{"timestamp": "2024-01-15T12:34:56.789123+00:00", "event": "connector_connected", "symbol": "BTCUSDT", "connector": "hft"}
{"timestamp": "2024-01-15T12:34:56.812456+00:00", "event": "connector_trade", "price": 100.5, "qty": 0.25, "side": "buy", "lag_ms": 10.2, "queue_size": 0, "connector": "hft"}
{"timestamp": "2024-01-15T12:34:56.850789+00:00", "event": "connector_depth", "lag_ms": 8.5, "queue_size": 0, "bids": 10, "asks": 10, "connector": "hft"}
```

## Reconnection Logic

When the connector loses connection:

1. **Initial Connection**: Tries to connect with exponential backoff (0.5s, 1s, 2s, 4s, 8s)
2. **Max Retries**: Default 5 attempts, then gives up
3. **Detection**: Monitors `is_connected()` every network loop iteration
4. **Auto-Reconnect**: Automatically attempts to reconnect with same backoff strategy

## Testing

### Running Tests

```bash
# All connector tests
python -m pytest backend/app/tests/test_hft_connector.py -v

# Parse logic tests
python -m pytest backend/app/tests/test_hft_connector.py::TestConnectorParsing -v

# Stubbed connector tests
python -m pytest backend/app/tests/test_hft_connector.py::TestStubbedConnector -v

# Stream adapter tests
python -m pytest backend/app/tests/test_hft_connector.py::TestHFTConnectorStream -v

# Configuration tests
python -m pytest backend/app/tests/test_hft_connector.py::TestConnectorConfiguration -v
```

### Test Coverage

26 tests cover:
- Trade/depth event parsing with various timestamp formats
- Connector lifecycle (connect/disconnect)
- Event subscription management
- Trade and depth event generation
- Stream startup/shutdown
- Metrics collection
- Health status reporting
- Reconnection behavior
- Strategy engine integration
- Configuration parsing

## Integration with Strategy Engine

The connector stream automatically forwards trades to the strategy engine:

```python
# In HFTConnectorStream._handle_trade_event()
if self._strategy_engine:
    self._strategy_engine.ingest_trade(tick)
```

This allows strategies to receive live trades from the connector feed just like they would from Binance WebSocket.

## Integration with Context Service

Trades are also forwarded to the context service for historical analysis:

```python
# In HFTConnectorStream._handle_trade_event()
if self.context_service:
    self.context_service.ingest_trade(tick)
```

## Advanced: Custom Connector Implementation

### Example: Binance Live Wrapper Integration

```python
from app.data_sources.hft_connector import ConnectorWrapper
import hftbacktest as hbt

class BinanceHFTConnector(ConnectorWrapper):
    def __init__(self, settings):
        self.settings = settings
        self.hft_live = hbt.BinanceLiveWrapper(
            symbol=settings.symbol,
            paper_trading=settings.connector_paper_trading
        )
        
    async def connect(self) -> None:
        await self.hft_live.connect()
        
    async def subscribe_trades(self, symbol: str) -> None:
        await self.hft_live.subscribe_trades(symbol)
        
    async def next_event(self) -> Optional[dict]:
        raw_event = await self.hft_live.next_message()
        
        if raw_event.get('type') == 'trade':
            return {
                'type': 'trade',
                'timestamp': raw_event['ts'],
                'price': raw_event['price'],
                'qty': raw_event['qty'],
                'side': 'buy' if raw_event['side'] == 0 else 'sell',
                'is_buyer_maker': raw_event['maker'],
                'id': raw_event['id']
            }
        
        return None
```

## Troubleshooting

### Connector Not Connecting

**Check logs**: Look for "connector_connection_error" events in logs

**Verify settings**: Ensure `DATA_SOURCE=hft_connector` is set

**Health check**: Call `/ws/health` endpoint to check connection status

### Events Not Received

**Check subscriptions**: Ensure `subscribe_trades()` and `subscribe_depth()` are called

**Verify event format**: Events must have correct field names and types

**Check logs**: Look for "connector_trade_parse_error" or similar

### High Latency

**Check queue size**: If queue_size is high, connector is slower than ingestion

**Monitor CPU**: Ensure sufficient CPU resources for event processing

**Increase buffer**: Adjust `MAX_QUEUE` environment variable

## Future Enhancements

- Multi-connector support (aggregate from multiple exchanges)
- Event filtering/transformation
- Rate limiting per connector
- Backup connector failover
- Connector-specific metrics (requests/sec, errors, latency percentiles)
