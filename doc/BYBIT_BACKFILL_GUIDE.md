# Bybit Backfill Implementation Guide

## Overview

The Bybit backfill implementation provides a drop-in replacement for Binance backfill, using the hftbacktest REST wrapper pattern to fetch historical trade data from Bybit's REST API. It maintains the same interface, caching semantics, and performance targets as the existing Binance implementation.

## Architecture

### Components

1. **BybitHttpClient**
   - HTTP client with proper headers, session management, and retry logic
   - Optional HMAC-SHA256 authentication for higher rate limits
   - Circuit breaker pattern for rate limit protection
   - Dynamic throttling and concurrency adjustment

2. **BybitConnectorHistory**
   - Main backfill class implementing the same interface as BinanceTradeHistory
   - Parallel chunked processing with configurable concurrency
   - Smart cache integration with resume capability
   - Trade field normalization from Bybit format to TradeTick schema

3. **Configuration Integration**
   - New settings for Bybit API endpoints and credentials
   - Rate limit and timeout configuration
   - Concurrency and performance tuning parameters

## Configuration

### Environment Variables

```bash
# Data source selection
DATA_SOURCE=bybit  # Switch from "binance_ws" to "bybit"

# Bybit API credentials (optional but recommended)
BYBIT_API_KEY=your_bybit_api_key_here
BYBIT_API_SECRET=your_bybit_api_secret_here

# Bybit API endpoints
BYBIT_REST_BASE_URL=https://api.bybit.com
BYBIT_API_TIMEOUT=30

# Rate limit and retry settings
BYBIT_BACKFILL_MAX_RETRIES=5
BYBIT_BACKFILL_RETRY_BASE=0.5
BYBIT_BACKFILL_RATE_LIMIT_THRESHOLD=3
BYBIT_BACKFILL_COOLDOWN_SECONDS=60
BYBIT_BACKFILL_PUBLIC_DELAY_MS=50

# Performance tuning
BYBIT_BACKFILL_MAX_CONCURRENT_CHUNKS=8
```

### Rate Limits

- **Public endpoints**: 600 requests per minute
- **Authenticated endpoints**: 1200 requests per minute (with API key)
- **Circuit breaker**: Opens after 3 consecutive rate limit errors
- **Cooldown**: 60 seconds when circuit breaker opens

## Performance Targets

### Benchmarks (72 chunks, 12 hours)

- **Authenticated mode**: ~8-12 seconds (8 concurrent chunks, no delays)
- **Public mode**: ~12-18 seconds (4 concurrent chunks, 50ms delays)
- **With cache resume**: ~2-5 seconds (only new data downloaded)

### Concurrency Strategy

```python
# Authenticated mode (API key provided)
max_concurrent_chunks = 8
request_delay = 0.0  # No delay needed

# Public mode (no API key)
max_concurrent_chunks = 4  # Conservative
request_delay = 0.05  # 50ms between requests
```

## API Integration

### Bybit Public Trades Endpoint

**URL**: `/v5/market/recent-trade`

**Parameters**:
- `category`: "linear" (for USDT perpetuals)
- `symbol`: Trading pair (e.g., "BTCUSDT")
- `limit`: 1000 (maximum)
- `start`: Start time in milliseconds
- `end`: End time in milliseconds

**Response Format**:
```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "list": [
      {
        "execId": "string",
        "symbol": "BTCUSDT",
        "price": "50000.0",
        "size": "0.1",
        "side": "Buy",
        "time": "1640995200000",
        "isBlockTrade": false
      }
    ]
  }
}
```

### Bybit Private Trades Endpoint

**URL**: `/v5/execution/list`

**Authentication**: HMAC-SHA256 signature required

**Additional Parameters**:
- `startTime`: Start time in milliseconds
- `endTime`: End time in milliseconds

**Response Format**:
```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "list": [
      {
        "symbol": "BTCUSDT",
        "execId": "string",
        "orderId": "string",
        "side": "Buy",
        "execPrice": "50000.0",
        "execQty": "0.1",
        "execTime": "1640995200000"
      }
    ]
  }
}
```

## Trade Field Normalization

### Bybit → TradeTick Mapping

| Bybit Field | TradeTick Field | Notes |
|-------------|-----------------|--------|
| `execTime`/`time` | `ts` | Milliseconds to datetime conversion |
| `execPrice`/`price` | `price` | String to float conversion |
| `execQty`/`size` | `qty` | String to float conversion |
| `side` | `side` | "Buy"/"Sell" → "buy"/"sell" |
| `execId` | `id` | String to int (hash fallback) |
| N/A | `isBuyerMaker` | Inferred: Buy=taker, Sell=maker |

### Side Logic

```python
# Bybit side indicates aggressor (taker)
side = "buy" if side_str.upper() == "BUY" else "sell"
# Maker is opposite of taker
is_buyer_maker = side == "sell"
```

## Cache Integration

### File Naming

- **Format**: `backfill_YYYY-MM-DD.parquet`
- **Location**: `BACKFILL_CACHE_DIR` (default: `./context_history_dir/backfill_cache`)
- **Schema**: Bybit-specific dict format with string IDs

### Cache Schema

```python
{
    "T": int,      # Timestamp in milliseconds
    "i": str,      # Trade ID as string (Bybit format)
    "p": float,     # Price
    "q": float,     # Quantity
    "s": str,      # Side ("buy"/"sell")
    "m": bool,      # isBuyerMaker
}
```

### Resume Logic

1. **Cache Hit**: Load existing trades, check last timestamp
2. **Gap Detection**: Calculate time since last cached trade
3. **Incremental Download**: Fetch only new data since cache
4. **Merge & Deduplicate**: Combine cached and new trades
5. **Cache Update**: Save merged dataset for next run

## Circuit Breaker Pattern

### States

- **CLOSED**: Normal operation, monitoring for errors
- **OPEN**: Rate limited, enforcing cooldown period
- **HALF_OPEN**: Testing recovery after cooldown

### Triggers

- **HTTP 429**: Rate limit exceeded
- **HTTP 10001**: Bybit-specific rate limit
- **Consecutive errors**: 3+ rate limit errors

### Recovery

1. **Throttle multiplier**: Increases on errors (1.0 → 5.0x)
2. **Concurrency reduction**: Dynamically reduces parallel requests
3. **Progressive recovery**: Gradually decreases throttle on success
4. **Cooldown timer**: 60-second pause when circuit opens

## Error Handling

### Retry Strategy

- **Exponential backoff**: 0.5s, 1s, 2s, 4s with jitter
- **Max retries**: 5 attempts per request
- **Circuit breaker**: Opens after threshold errors
- **Graceful degradation**: Falls back to public endpoints on auth failure

### Fallback Behavior

```python
# Authentication error → switch to public mode
if resp.status in {401, 403}:
    logger.warning("Bybit API authentication failed, switching to public endpoints")
    self.use_auth = False
    return await self.fetch_public_trades(...)
```

## Testing

### Unit Tests

**File**: `test_bybit_backfill.py`

**Coverage**:
- HTTP client authentication and headers
- Circuit breaker state transitions
- Trade parsing (public and private formats)
- Pagination and time window handling
- Cache integration and resume
- Error handling and retry logic
- Performance benchmarks

### Mock Strategy

```python
# Mock HTTP responses
mock_response_data = {
    "retCode": 0,
    "retMsg": "OK",
    "result": {"list": [mock_trades]}
}

# Test rate limit scenarios
mock_rate_limit_response = AsyncMock()
mock_rate_limit_response.status = 429
```

### Cache Tests

**File**: `test_backfill_cache.py` (TestBybitCacheIntegration)

**Coverage**:
- Trade tick ↔ dict conversion
- Cache persistence and resume
- Deduplication logic
- File naming conventions

## Usage Examples

### Basic Bybit Backfill

```python
from app.context.backfill import BybitConnectorHistory
from app.ws.models import Settings

# Configure for Bybit
settings = Settings(
    data_source="bybit",
    bybit_api_key="your_key",
    bybit_api_secret="your_secret",
    backfill_cache_enabled=True
)

# Create history provider
history = BybitConnectorHistory(settings)

# Fetch trades
trades = []
async for trade in history.iterate_trades(start_dt, end_dt):
    trades.append(trade)
```

### Cache-Aware Backfill

```python
# Use smart cache resume
trades = await history.backfill_with_cache(start_dt, end_dt)

# Handles:
# - Cache detection and loading
# - Gap analysis and incremental download
# - Merge and deduplication
# - Cache update
```

### Test Mode

```python
# Enable test mode for authentication testing
settings.context_backfill_test_mode = True

history = BybitConnectorHistory(settings)
test_trades = await history.test_single_window()
```

## Migration Guide

### From Binance to Bybit

1. **Update environment**:
   ```bash
   DATA_SOURCE=bybit
   BYBIT_API_KEY=your_key
   BYBIT_API_SECRET=your_secret
   ```

2. **Code changes**:
   ```python
   # Old
   from app.context.backfill import BinanceTradeHistory
   
   # New (automatic via context service)
   # Uses BybitConnectorHistory when DATA_SOURCE=bybit
   ```

3. **Cache migration**:
   - Binance cache files use different schema
   - New Bybit cache files created automatically
   - Old cache files ignored (not compatible)

### Configuration Mapping

| Binance Setting | Bybit Setting | Default |
|-----------------|-----------------|----------|
| `BINANCE_API_KEY` | `BYBIT_API_KEY` | None |
| `BINANCE_API_SECRET` | `BYBIT_API_SECRET` | None |
| `BINANCE_API_TIMEOUT` | `BYBIT_API_TIMEOUT` | 30 |
| `BACKFILL_MAX_RETRIES` | `BYBIT_BACKFILL_MAX_RETRIES` | 5 |
| `BACKFILL_RATE_LIMIT_THRESHOLD` | `BYBIT_BACKFILL_RATE_LIMIT_THRESHOLD` | 3 |
| `BACKFILL_PUBLIC_DELAY_MS` | `BYBIT_BACKFILL_PUBLIC_DELAY_MS` | 50 |
| N/A | `BYBIT_BACKFILL_MAX_CONCURRENT_CHUNKS` | 8 |

## Troubleshooting

### Common Issues

1. **Authentication failures**
   - Check API key and secret are correct
   - Ensure API key has required permissions
   - Verify IP whitelist (if enabled)

2. **Rate limiting**
   - Reduce `BYBIT_BACKFILL_MAX_CONCURRENT_CHUNKS`
   - Increase `BYBIT_BACKFILL_PUBLIC_DELAY_MS`
   - Use authenticated endpoints for higher limits

3. **Cache issues**
   - Check `BACKFILL_CACHE_DIR` permissions
   - Verify sufficient disk space
   - Clear cache if corrupted

4. **Performance issues**
   - Monitor circuit breaker state
   - Check throttle multiplier in logs
   - Adjust concurrency based on rate limits

### Debug Logging

```python
# Enable detailed logging
import logging
logging.getLogger("context.backfill").setLevel(logging.DEBUG)

# Key log messages to watch for:
# "Bybit circuit breaker opened"
# "throttle: 2.3x" 
# "Bybit backfill complete"
```

### Performance Monitoring

```python
# Monitor key metrics
- Chunk processing rate
- Circuit breaker state changes
- Throttle multiplier adjustments
- Cache hit/miss ratios
- Error rates by type
```

## Future Enhancements

### Planned Features

1. **WebSocket Integration**: Real-time Bybit data streaming
2. **Enhanced Caching**: Multi-level cache with compression
3. **Adaptive Rate Limits**: Dynamic limit detection and adjustment
4. **Historical Data**: Support for longer time windows
5. **Metrics Dashboard**: Real-time performance monitoring

### Extension Points

- **Custom Connectors**: Implement additional exchanges
- **Authentication Methods**: Support for API keys, OAuth, etc.
- **Data Transformations**: Custom trade field mappings
- **Storage Backends**: Alternative cache implementations