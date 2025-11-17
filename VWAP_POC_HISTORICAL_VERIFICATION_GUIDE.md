# VWAP/POCd Historical Verification Guide

## Overview

This guide explains how to verify that VWAP, POCd, VAH/VAL values are calculated accurately from pure historical data without contamination from live WebSocket data.

## Critical Issue Fixed

**Problem**: VWAP and POCd values were potentially inaccurate due to:
- Live WebSocket trades being mixed with backfill data
- Backfill not retrieving 100% of day's trades
- Calculations mixing historical + live data instead of pure historical

**Solution**: Implemented multiple verification mechanisms:
1. **Live Data Disable Mode**: Reject live trades during/after backfill
2. **Trade Source Tracking**: Track origin of each trade (backfill vs live)
3. **Comprehensive Logging**: Detailed logs showing exactly what data was used
4. **Verification Endpoints**: Debug endpoints to verify calculations match expectations

## Configuration

### Enable Historical-Only Mode

```bash
# In your .env file:
CONTEXT_DISABLE_LIVE_DATA=true
```

This mode:
- Accepts trades from backfill during startup
- Rejects ALL live WebSocket trades after backfill begins
- Allows you to test with 100% pure historical data only

### Alternative: Pure Historical Mode

```bash
# For pure backtesting without any live data interference:
CONTEXT_HISTORICAL_ONLY_MODE=true
```

## Verification Endpoints

After backfill completes, use these endpoints to verify metrics:

### 1. Trade Source Statistics
```bash
curl http://localhost:8000/context/debug/trades
```

Response shows:
- `trades_from_backfill`: How many trades came from historical backfill
- `trades_from_live`: How many trades came from live WebSocket
- `live_trades_rejected`: How many live trades were rejected (if live data disabled)
- `backfill_percentage` / `live_percentage`: Percentages of each source

Example:
```json
{
  "summary": {
    "total_trades": 45000,
    "trades_from_backfill": 45000,
    "trades_from_live": 0,
    "live_trades_rejected": 0,
    "backfill_percentage": 100.0,
    "live_percentage": 0.0
  },
  "configuration": {
    "context_disable_live_data": true,
    "context_historical_only_mode": false,
    "backfill_complete": true
  },
  "vwap_debug": {
    "sum_price_qty": 12345678.90,
    "sum_qty": 45000.0,
    "vwap": "42567.89",
    "poc": "42600.00"
  },
  "volumes": {
    "total_volume": 45000.0,
    "pre_market_buy": 15000.0,
    "pre_market_sell": 10000.0,
    "live_buy": 10000.0,
    "live_sell": 10000.0
  }
}
```

### 2. Backfill Status
```bash
curl http://localhost:8000/context/backfill/status
```

Shows:
- `status`: "complete", "in_progress", "error", etc.
- `progress`: Percentage complete and trade count
- Total trades downloaded from historical API

### 3. VWAP/POC Verification
```bash
curl http://localhost:8000/context/debug/recalculate-verification
```

This endpoint:
- Recalculates VWAP from scratch using current volume data
- Recalculates POC from scratch
- Compares current values with recalculated values
- Shows if they match (verification passed)

Response:
```json
{
  "verification": {
    "vwap": {
      "current": "42567.890000",
      "recalculated": "42567.890000",
      "match": true
    },
    "poc": {
      "current": "42600.000000",
      "recalculated": "42600.000000",
      "match": true
    }
  },
  "volume_profile": {
    "total_volume": "45000.0",
    "price_levels": 850,
    "day_high": "43500.00",
    "day_low": "41200.00"
  },
  "data_integrity": {
    "sum_price_qty_matches": true,
    "sum_qty_matches": true
  }
}
```

### 4. Main Context Levels
```bash
curl http://localhost:8000/context/levels
```

Shows current trading levels:
- `VWAP`: Current day VWAP
- `POCd`: Current day Point of Control
- `VAH/VAL`: Value Area High/Low
- `VWAPprev/POCprev/VAHprev/VALprev`: Previous day levels

### 5. Debug VWAP Details
```bash
curl http://localhost:8000/context/debug/vwap
```

Shows:
- `sum_price_qty`: Sum of (price × quantity) for all trades
- `sum_qty`: Sum of all quantities
- `trade_count`: Total trades processed
- VWAP calculation = sum_price_qty / sum_qty

### 6. Debug POC Details
```bash
curl http://localhost:8000/context/debug/poc
```

Shows:
- `bin_size`: Price bin size for volume profile
- `top_bins`: Top 10 price levels by volume
- `poc_price`: Point of Control price
- `poc_volume`: Volume at POC

## VAH/VAL Calculation Verification

The VAH/VAL (Value Area High/Low) uses the **correct algorithm**:

1. **Start**: Find POC (price level with highest volume)
2. **Calculate target**: 70% of total day volume
3. **Expand outward**: From POC, expand to adjacent price levels (both up and down)
4. **Expand to higher volume side first**: Alternate between lower and higher adjacent bins
5. **Continue**: Until cumulative volume reaches 70%
6. **VAH**: Highest price in value area
7. **VAL**: Lowest price in value area

This creates a **contiguous volume profile** - no gaps - matching TradingView and Bybit methodology.

## Testing Against External Sources

### Comparison with TradingView

1. Open TradingView chart for same symbol
2. Add Volume Profile indicator
3. Compare:
   - POC value: Should match exactly (within tick size)
   - VAH/VAL: Should match exactly (within tick size)
   - VWAP: Should match exactly (within rounding)

### Comparison with Bybit

1. Open Bybit chart for same symbol
2. Check "Volume Profile" indicator
3. Compare same metrics
4. Note: If you see difference within ±0.1%, it's acceptable rounding

## Backfill Verification Logs

After startup, check logs for this section:

```
✅ Backfill complete! TRADING NOW ENABLED
📊 Metrics are now PRECISE and ready for trading
📈 BACKFILL SUMMARY - Trades=45000 (backfill=45000, live=0, rejected=0), 
   VWAP=42567.890000, POC=42600.000000, Volume=45000.0, DayHigh=43500.0, DayLow=41200.0
💼 VOLUMES - PreMarketBuy=15000.0, PreMarketSell=10000.0, LiveBuy=10000.0, LiveSell=10000.0
📊 PREVIOUS DAY - PDH=43600.0, PDL=40800.0, VAH=43200.0, VAL=41500.0, POC=42800.0, VWAP=42300.0
```

This shows:
- ✅ All 45,000 trades from backfill (0 from live, 0 rejected) = Pure historical data
- Calculated metrics with full precision
- Previous day levels for reference

## Troubleshooting

### Issue: Some live trades still appearing?

1. Verify `CONTEXT_DISABLE_LIVE_DATA=true` in environment
2. Check `/context/debug/trades` endpoint
3. Look for `trades_from_live > 0` or `live_trades_rejected > 0`
4. If trades from live > 0 and should be 0, backfill hasn't completed yet

### Issue: VWAP/POC don't match TradingView?

1. Check `/context/debug/recalculate-verification` - should show `"match": true`
2. If match is false, there's a calculation issue
3. Verify tick size: `/context/debug/exchangeinfo` - should match symbol's tick size
4. Check volume count: `/context/debug/trades` - should show correct trade count

### Issue: Previous day levels missing?

1. Check if previous day cache file exists: `./data/history/BTCUSDT_2024-11-16_profile.parquet`
2. Verify `CONTEXT_BOOTSTRAP_PREV_DAY=true` in environment
3. Check backfill logs for "Loaded previous day" message
4. If file missing, previous day's backfill may have failed

### Issue: Backfill times out or fails?

1. Check logs for rate limit errors (Bybit: 418, Binance: 429)
2. Increase timeout: `BACKFILL_TIMEOUT_SECONDS=300`
3. Check network connectivity
4. Verify API credentials if using authenticated backfill

## Best Practices

1. **Always verify on startup**: Check logs for the backfill summary
2. **Use `/debug/trades` regularly**: Monitor trade source ratios
3. **Compare externally**: Match against TradingView/Bybit weekly
4. **Enable `CONTEXT_DISABLE_LIVE_DATA` for testing**: Get pure historical results
5. **Keep previous day profiles**: Don't delete `./data/history/` directory
6. **Disable live data during critical testing**: Don't mix sources when verifying
7. **Monitor backfill progress**: Check logs show expected trade counts

## Implementation Details

### Trade Source Tracking

Each trade is marked with origin when ingested:

```python
# From backfill:
self.ingest_trade(trade, from_backfill=True)

# From live WebSocket:
self.ingest_trade(trade, from_backfill=False)
```

Counter increments:
- `trades_from_backfill`: Historical trades from API
- `trades_from_live`: Real-time trades from WebSocket
- `live_trades_rejected`: Rejected when `CONTEXT_DISABLE_LIVE_DATA=true`

### VWAP Calculation

Running total calculation (numerator and denominator):
```python
self.sum_price_qty_base += price * quantity
self.sum_qty_base += quantity
VWAP = sum_price_qty_base / sum_qty_base
```

Verified against recalculation from volume map:
```python
total_price_qty = sum(price * volume for price, volume in volume_by_price.items())
total_qty = sum(volume_by_price.values())
VWAP_check = total_price_qty / total_qty
```

### POC Calculation

- Bins prices by tick size
- Groups trades by price bin
- Finds bin with maximum cumulative volume
- Returns price of that bin

### Previous Day Profile Storage

- Stored as Parquet file: `{SYMBOL}_{DATE}_profile.parquet`
- Contains: `price` (binned), `volume` (accumulated)
- Used to calculate PDH/PDL/VAH/VAL/POC/VWAP for "prev" levels
- Automatically persisted at day roll

## Summary

This implementation provides:
✅ **Pure historical mode** - disable live data for testing
✅ **Trade source tracking** - know exactly where data comes from
✅ **Detailed logging** - see exact numbers in logs
✅ **Verification endpoints** - programmatically verify calculations
✅ **Recalculation checking** - confirm VWAP/POC match expectations
✅ **Data integrity validation** - detect inconsistencies
✅ **Previous day persistence** - VAH/VAL calculation from cache

Use this to achieve **100% precision** for VWAP, POCd, VAH/VAL/POCprev metrics.
