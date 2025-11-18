# WebSocket Trades Fix - Summary

## Problem
The WebSocket was receiving trades from Binance (visible in logs as `trade_tick` events), but trades were NOT being saved to the `TradeService` buffer. The `/trades` endpoint was returning an empty array `[]`, and `/trades/stats` was showing `total_count=0`.

## Root Cause
The architecture had TWO separate systems that were not connected:

1. **WSModule** - Managed WebSocket streams (Binance/Bybit) and received trades correctly
2. **TradeService** - Had its own buffer but was creating NEW instances on every API request via dependency injection
3. The `/trades` endpoint used the dependency injection which created empty TradeService instances
4. No mechanism existed to forward trades from the WebSocket streams to TradeService

## Solution
Created a **centralized TradeService singleton** that is shared between the WebSocket streams and the API endpoints:

### Changes Made

#### 1. WSModule (`backend/app/ws/routes.py`)
- Added `TradeService` as a singleton instance in `WSModule.__init__`
- Connected the trade service to both Bybit and Binance streams
- Now all WebSocket streams forward trades to the shared TradeService

#### 2. BybitWebSocketConnector (`backend/app/connectors/bybit_websocket.py`)
- Added `on_trade_callback` parameter to constructor
- Modified `_process_trades()` to call the callback when trades are received
- This allows the connector to forward trades to external systems

#### 3. BybitWebSocketStream (`backend/app/connectors/bybit_websocket.py`)
- Added `set_trade_service()` method to receive the TradeService reference
- Added `_on_trade_received()` callback that forwards trades to TradeService
- Passes the callback to BybitWebSocketConnector during initialization

#### 4. TradeStream (Binance) (`backend/app/ws/trades.py`)
- Added `set_trade_service()` method to receive the TradeService reference
- Modified `handle_payload()` to forward trades to TradeService after processing
- Converts TradeTick to the standard trade dictionary format

#### 5. TradeService (`backend/app/services/trade_service.py`)
- Added logging to `add_trade()` method for debugging
- Logs show: "Trade added: price=X, qty=Y, side=Z, buffer_size=N"
- Updated `get_stats()` to not rely on connector status (uses buffer data only)

#### 6. Trades Router (`backend/app/routers/trades.py`)
- Changed `get_trade_service()` dependency to return the singleton from WSModule
- No longer creates new TradeService instances on every request
- Now returns trades from the shared buffer

## Testing Results

### ✅ All Tests Passed

```bash
# 1. Trades endpoint returns data
$ curl http://localhost:8000/trades | jq 'length'
100  # Returns 100 trades (default limit)

# 2. Stats show real trade count
$ curl http://localhost:8000/trades/stats | jq
{
  "total_count": 182,
  "oldest_trade_time": "2025-11-18T13:11:22.309000+00:00",
  "newest_trade_time": "2025-11-18T13:11:43.542000+00:00",
  "buffer_size": 5000
}

# 3. Trade count increases over time
$ curl http://localhost:8000/trades/stats | jq '.total_count'
229  # Increased from 182 in 3 seconds

# 4. Trade structure is correct
$ curl http://localhost:8000/trades | jq '.[0]'
{
  "price": 91494.9,
  "qty": 0.005,
  "side": "Buy",
  "time": "2025-11-18T13:11:59.570000+00:00",
  "symbol": "BTCUSDT",
  "trade_id": "2960628770"
}

# 5. Logs show trades being added
Trade added: price=91492.3, qty=0.025, side=Sell, buffer_size=63
Trade added: price=91492.4, qty=0.002, side=Buy, buffer_size=64
...
```

## Acceptance Criteria Met

- ✅ GET /trades returns trades reales (length > 0)
- ✅ Cada trade tiene: price, qty, side, time, symbol, trade_id
- ✅ GET /trades/stats muestra total_count > 0
- ✅ Logs muestran "Trade added:" cuando llega trade_tick
- ✅ Buffer se actualiza cada 1-2 segundos (verified with increasing trade count)
- ✅ No crashes or errors in backend

## Architecture Diagram

```
┌─────────────────┐
│   FastAPI App   │
└────────┬────────┘
         │
         ├─ WSModule (Singleton)
         │  ├─ TradeService (Singleton Buffer)
         │  ├─ BybitWebSocketStream ──┐
         │  │  └─ BybitWebSocketConnector (receives trades)
         │  │                          │
         │  └─ TradeStream (Binance) ─┤
         │                             │
         │                        [Forwards trades]
         │                             │
         │                             ▼
         └─ /trades Router ──► TradeService.get_recent_trades()
            /trades/stats   ──► TradeService.get_stats()
```

## Data Flow

1. **WebSocket receives trade** (Binance or Bybit)
2. **Stream processes trade** (BybitWebSocketStream or TradeStream)
3. **Trade forwarded to callback** → `TradeService.add_trade()`
4. **Trade saved to buffer** (deque with max 5000 items)
5. **API endpoint reads from buffer** → Returns to client

## Benefits

1. **Single source of truth** - One shared buffer for all trades
2. **No duplication** - No more creating multiple TradeService instances
3. **Consistent data** - All endpoints return the same trade data
4. **Better logging** - Clear visibility into when trades are added
5. **Scalable** - Easy to add more data sources (just call TradeService.add_trade)

## Notes

- Buffer size is configurable via `MAX_QUEUE` env var (default: 5000)
- Works with both Binance and Bybit WebSocket sources
- Compatible with existing strategy engine forwarding
- No breaking changes to existing API contracts
