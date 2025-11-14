# Backfill and Previous Day Key Levels - Implementation Summary

## Overview
This document describes the implementation of backfill status tracking and previous day key levels calculation for the BotCrypto4 trading system.

## Ticket Requirements
1. **Backfill Status**: Verify and fix "Idle" status → should show proper progression
2. **Previous Day Metrics**: Calculate VALprev, VAHprev, POCprev from previous day
3. **Opening Range**: Implement correct OR calculation (08:00-08:10 UTC)
4. **Metrics Precision**: Show if we have sufficient data
5. **Backend Status**: Update health to "Healthy" when backfill completes

## Implementation Details

### 1. Backfill Status Tracking

#### Status States
- **idle**: Initial state (should not be seen after startup)
- **pending**: Backfill task created, about to start
- **in_progress**: Actively downloading historical data
- **complete**: Backfill finished successfully
- **skipped**: Using hft_connector (doesn't need backfill)
- **disabled**: CONTEXT_BACKFILL_ENABLED=false
- **error**: Backfill failed
- **cancelled**: Shutdown during backfill

#### Key Changes
- **service.py line 161**: Set status to "pending" when creating backfill task
- **service.py line 603**: Set status to "in_progress" when backfill starts
- **service.py line 626**: Set status to "complete" when backfill finishes
- **main.py line 73-87**: Updated `/health` endpoint to include backfill status
- **api-client.ts line 33**: Frontend now uses `/ready` endpoint for full status

### 2. Previous Day Key Levels

#### Calculated Metrics
- **VAHprev**: Value Area High (top of 70% volume area)
- **VALprev**: Value Area Low (bottom of 70% volume area)
- **POCprev**: Point of Control (price with highest volume)
- **PDH**: Previous Day High
- **PDL**: Previous Day Low
- **VWAPprev**: Volume-Weighted Average Price of previous day

#### Calculation Logic
Previous day levels are calculated from trades between 00:00-23:59:59 UTC of the previous day.

**Method: `_profile_from_volume()`** (service.py line 900-941)
1. Calculate POC: Price level with highest volume
2. Calculate VAH/VAL: 
   - Sort all prices by volume (descending)
   - Add volumes until reaching 70% of total volume
   - VAH = highest price in this set
   - VAL = lowest price in this set
3. Calculate VWAP: Sum(price × volume) / Sum(volume)

#### Loading Strategy
The system tries multiple sources in order:

1. **Backfill**: Load from historical trades during `_populate_previous_day()` (line 813-848)
2. **Profile Cache**: Load from `{symbol}_{date}_profile.parquet` (line 541-606)
3. **Trade History**: Load from `{symbol}_{date}.parquet` (line 568-606)

#### Persistence
Volume profiles are automatically saved after each day:
- **Method**: `_persist_prev_day_profile()` (line 522-539)
- **Trigger**: When day rolls over in `_roll_day()` (line 473-479)
- **Trigger**: When previous day backfill completes (line 936-938)
- **Format**: Parquet file with columns: `price`, `volume`
- **Location**: `CONTEXT_HISTORY_DIR/{symbol}_{date}_profile.parquet`

### 3. Opening Range (OR)

#### Configuration
- **Start Time**: 08:00 UTC (London session open)
- **End Time**: 08:10 UTC (10 minutes duration)
- **Timestamps**: `or_start`, `or_end` (service.py line 482-483)

#### Tracking
Opening Range high/low are tracked in real-time during trade ingestion:

**Method: `ingest_trade()`** (service.py line 231-235)
```python
if self.or_start and self.or_end and self.or_start <= trade_ts < self.or_end:
    if self.or_high is None or price > self.or_high:
        self.or_high = price
    if self.or_low is None or price < self.or_low:
        self.or_low = price
```

#### Frontend Display
- **OR High**: Solid yellow line (`#facc15`)
- **OR Low**: Dashed yellow line (`#facc15`)
- **Labels**: "OR High", "OR Low"

### 4. Metrics Precision

The system now properly reports metrics precision based on backfill status:

**In `/ready` endpoint** (main.py line 90-113):
- `IMPRECISE (backfill X%)` - While backfill is running
- `IMPRECISE (backfill pending)` - Before backfill starts
- `PRECISE` - After backfill completes

### 5. Health Status

#### `/health` Endpoint
Updated to include backfill information (main.py line 73-87):
```json
{
  "status": "ok" | "degraded",
  "backfill_status": "complete" | "in_progress" | ...,
  "backfill_complete": true | false
}
```

System is considered "healthy" when backfill status is one of:
- `complete`
- `skipped`
- `disabled`

#### `/ready` Endpoint
Comprehensive status including:
- Session information
- Trading enabled/disabled
- Backfill progress (current/total chunks, percentage, ETA)
- Metrics precision

### 6. Frontend Updates

#### Types (types.ts line 41-51)
Added `VWAPprev` to `ContextLevels` type.

#### Dashboard (dashboard-client.tsx line 883-890)
Added Previous VWAP line to chart overlays.

#### API Client (api-client.ts line 33)
Changed from `/health` to `/ready` endpoint for comprehensive status.

## Configuration

### Environment Variables

```bash
# Backfill Configuration
CONTEXT_BACKFILL_ENABLED=true          # Enable/disable backfill
CONTEXT_BOOTSTRAP_PREV_DAY=true        # Load previous day levels
BACKFILL_CACHE_ENABLED=true            # Enable Parquet cache
BACKFILL_TIMEOUT_SECONDS=180           # Max time for backfill

# Directories
CONTEXT_HISTORY_DIR=./data/history     # Where profiles are saved
BACKFILL_CACHE_DIR=./context_history_dir/backfill_cache  # Trade cache

# Calculation
PROFILE_TICK_SIZE=0.1                  # Price binning granularity

# Data Source
DATA_SOURCE=binance_ws                 # binance_ws | bybit | hft_connector
```

## Testing

### Manual Testing
1. Start backend: `cd backend && uvicorn app.main:app --reload`
2. Check backfill status: `curl http://localhost:8000/backfill/status`
3. Check key levels: `curl http://localhost:8000/levels`
4. Check health: `curl http://localhost:8000/ready`

### Expected Behavior

#### Successful Backfill
```
Background backfill: started
⚠️  TRADING DISABLED - Backfill in progress
Backfill: Dynamic range 00:00:00 → 14:23:45 (87 chunks, ~870 minutes)
Backfill progress: 10/87 chunks (11.5%)
...
Backfill complete: ~45000 trades in ~180s, 100% successful, VWAP=43250.23, POC=43200.0
Backfill: loading previous day from 2024-11-13T00:00:00 to 2024-11-13T23:59:59.999
Backfill previous day complete: trades=52340 volume=1234.567 PDH=43500.0 PDL=42800.0 VAH=43400.0 VAL=43000.0 POC=43200.0
✅ Backfill complete! TRADING NOW ENABLED
```

#### Using Cache
```
Backfill cache: HIT (52340 trades from 2024-11-13)
Gap detected: 2.3h since last cache. Downloading new data...
Downloaded 3245 new trades, merged with 52340 cached trades, total: 55585 after dedup
Loaded previous day profile from btcusdt_2024-11-13_profile.parquet
```

#### Skipped (HFT Connector)
```
Backfill: skipped (using hft_connector for live data)
Loaded previous day levels from cache: PDH=43500.0 PDL=42800.0 VAH=43400.0 VAL=43000.0 POC=43200.0
```

## Dashboard Display

The frontend dashboard now shows:

1. **Backfill Status Panel**
   - Trading: ✅ ENABLED / ❌ DISABLED
   - Backfill Status: ✅ Complete / ⏳ In Progress / etc.
   - Progress bar with percentage
   - Estimated time remaining
   - Metrics precision: PRECISE / IMPRECISE

2. **Price Chart with Key Levels**
   - Current VWAP (solid cyan)
   - Current POC (solid orange)
   - Opening Range High (solid yellow)
   - Opening Range Low (dashed yellow)
   - Previous VWAP (dashed light blue)
   - Previous Day High/Low (dashed light blue)
   - Previous VAH/VAL (dashed light blue)
   - Previous POC (dashed light blue)

## Files Modified

### Backend
- `backend/app/context/service.py`: Core backfill and calculation logic
- `backend/app/main.py`: Health endpoint updates
- `backend/app/context/backfill_cache.py`: (no changes, already supports caching)

### Frontend
- `frontend/app/types.ts`: Added VWAPprev type
- `frontend/app/api-client.ts`: Changed to use /ready endpoint
- `frontend/app/dashboard-client.tsx`: Added VWAPprev to chart overlays

## Troubleshooting

### Backfill Stays "Idle"
- Check `CONTEXT_BACKFILL_ENABLED=true` in environment
- Check `DATA_SOURCE` is not `hft_connector`
- Check backend logs for errors

### Previous Day Levels are N/A
- Ensure `CONTEXT_BOOTSTRAP_PREV_DAY=true`
- Check for profile files in `CONTEXT_HISTORY_DIR`
- Run backfill at least once to populate cache
- Check backend logs for loading attempts

### OR High/Low are N/A
- Ensure current time is after 08:10 UTC
- Check that trades are being ingested
- Verify trades have timestamps within OR window

## Performance Considerations

### Backfill Speed
- **Binance** with auth: ~20 concurrent chunks, ~2-3 minutes for 24h
- **Binance** public: ~3 concurrent chunks, ~5-8 minutes for 24h
- **Bybit** with auth: ~8 concurrent chunks, ~3-5 minutes for 24h

### Cache Benefits
- Profile load: ~10ms (vs 2-3 minutes full backfill)
- Daily restart: Instant (loads from cache)
- Resume after disconnect: Only downloads gap

### Memory Usage
- Volume map: ~5-10 MB per day (100k trades)
- Cache files: ~20-50 MB per day (Parquet compressed)

## Future Enhancements

1. **Multi-timeframe profiles**: Calculate and store 1h, 4h, weekly profiles
2. **Profile comparison**: Compare today's profile vs previous day
3. **Level touches**: Track when price touches key levels
4. **Level strength**: Calculate support/resistance strength
5. **Automated alerts**: Notify when price approaches key levels
