# Backfill Cache Guide

## Overview

The backfill cache system provides persistent storage of historical trade data in Parquet format. This dramatically reduces rate limit pressure on subsequent startup events by:

1. **Caching** - Saving downloaded trades to local Parquet files organized by date
2. **Smart Resume** - On next startup, only downloading new data since last cached timestamp
3. **Deduplication** - Automatically removing duplicate trades when merging old and new data
4. **Graceful Fallback** - If cache is missing or corrupt, automatically performs full backfill

## Performance Impact

### Without Cache (Current)
- **First startup**: ~5-10 minutes (full 16-hour backfill = ~97 chunks)
- **Second startup**: ~5-10 minutes (full backfill again - lots of rate limits!)
- **Subsequent startups**: Same as second (~5-10 minutes with rate limiting)
- **Risk**: Rate limit (418/429) errors after ~16 hours of downloading

### With Cache (New)
- **First startup**: ~5-10 minutes (full backfill, saves to cache)
- **Second startup**: ~10-30 seconds (cache + small gap download)
- **Subsequent startups**: ~10-30 seconds (cache + minimal new data)
- **Rate limit pressure**: Dramatically reduced - only new hours downloaded

## Configuration

### Environment Variables

```bash
# Enable/disable persistent cache (default: true)
BACKFILL_CACHE_ENABLED=true

# Directory to store cache files (default: ./context_history_dir/backfill_cache)
BACKFILL_CACHE_DIR=./context_history_dir/backfill_cache
```

### .env Example

```env
# Backfill cache settings
BACKFILL_CACHE_ENABLED=true
BACKFILL_CACHE_DIR=./context_history_dir/backfill_cache
```

### Programmatic Configuration

```python
from app.context.backfill import BinanceTradeHistory
from app.ws.models import Settings

# Caching enabled (default)
settings = Settings(
    backfill_cache_enabled=True,
    backfill_cache_dir="./my_cache_dir"
)
history = BinanceTradeHistory(settings)

# Caching disabled
settings = Settings(backfill_cache_enabled=False)
history = BinanceTradeHistory(settings)
```

## Cache File Structure

### Location
```
./context_history_dir/backfill_cache/
├── backfill_2025-11-07.parquet  (today's trades)
├── backfill_2025-11-06.parquet  (yesterday's trades)
└── backfill_2025-11-05.parquet  (older trades)
```

### File Format
- **Format**: Apache Parquet (columnar, compressed, efficient)
- **Compression**: Snappy (built-in to Polars)
- **Typical size**: ~40-50 MB per day (100k trades)
- **Read time**: <100ms for 100k trades

### Parquet Schema
```python
{
    "T": int64,        # Timestamp in milliseconds
    "a": int64,        # Aggregate trade ID
    "p": float64,      # Price
    "q": float64,      # Quantity
    "f": int64,        # First trade ID
    "l": int64,        # Last trade ID
    "m": bool,         # Is buyer maker
    "M": bool,         # Ignore (not used)
}
```

## Usage Flow

### Typical Startup Sequence

```
startup
  ├─ Check if backfill_2025-11-07.parquet exists
  ├─ If YES:
  │  ├─ Load 70,432 trades from cache (instant)
  │  ├─ Extract last_cached_ts = 2025-11-07 09:30:00 UTC
  │  ├─ Calculate gap = 3 hours (since last cache to now)
  │  ├─ If gap > 0:
  │  │  ├─ Download 15,000 new trades (2 parallel chunks, ~10s)
  │  │  ├─ Deduplicate by trade ID (removes 50 duplicates)
  │  │  └─ Merge: 70,432 + 15,000 - 50 = 85,382 total
  │  └─ Save merged trades back to cache (1.5s)
  └─ If NO:
     ├─ Full backfill: download 85,000 trades (~5-10 min)
     └─ Save to cache (1.5s)

Total time on resume: ~15 seconds vs ~5-10 minutes full backfill
Rate limit hits: ~2 (only for new data) vs ~20+ (full backfill)
```

## Logging

### Startup Logs

```log
INFO  Backfill cache manager initialized at ./context_history_dir/backfill_cache
INFO  Backfill: using cache + resume strategy from 2025-11-07T00:00:00+00:00 to 2025-11-07T12:30:45+00:00
INFO  Loaded 70432 trades from cache: backfill_2025-11-07.parquet
INFO  Cache found: 70,432 trades from 2025-11-07, gap since cache: 2.5h
INFO  Gap detected: 2.5h since last cache. Downloading new data...
INFO  Downloaded 15000 new trades, merged with 70432 cached trades, total: 85000 after dedup
INFO  Saved 85000 trades to backfill_2025-11-07.parquet (12.3 MB)
INFO  Backfill complete: trades=85000 VWAP=103.176 POC=102.979 rangeToday=500.5 cd_pre=12345.6
```

### Cache Miss Log

```log
INFO  Backfill cache manager initialized at ./context_history_dir/backfill_cache
INFO  Backfill: using cache + resume strategy from 2025-11-08T00:00:00+00:00 to 2025-11-08T08:15:30+00:00
INFO  No cache for 2025-11-08, doing full backfill
INFO  Backfill: 51 chunks (10 min each), max 5 concurrent from 2025-11-08T00:00:00+00:00 to 2025-11-08T08:15:30+00:00
...
INFO  Saved 65000 trades to backfill_2025-11-08.parquet (14.2 MB)
INFO  Backfill complete: trades=65000 VWAP=103.890 POC=103.445 rangeToday=485.2 cd_pre=8923.1
```

### Cache Disabled Log

```log
INFO  Backfill cache: disabled
INFO  Backfill: downloading trades from 2025-11-07T00:00:00+00:00 to 2025-11-07T12:30:45+00:00
INFO  Backfill: 75 chunks (10 min each), max 5 concurrent...
...
```

## Maintenance

### Cache Cleanup

To remove cache files older than 5 days:

```python
from app.context.backfill_cache import BackfillCacheManager

manager = BackfillCacheManager("./context_history_dir/backfill_cache")
manager.cleanup_old_cache(keep_days=5)  # Keep last 5 days, delete older
```

This removes cache files that are more than 5 days old to save disk space.

### Manual Cache Clearing

```bash
# Remove all cache files
rm -rf ./context_history_dir/backfill_cache/

# Remove specific date
rm ./context_history_dir/backfill_cache/backfill_2025-11-07.parquet
```

### Disable Caching Temporarily

```bash
# Set environment variable
export BACKFILL_CACHE_ENABLED=false

# Run your application
python -m app.main
```

## Advanced Usage

### Direct Cache Access

```python
from app.context.backfill_cache import BackfillCacheManager
from datetime import datetime, timezone

manager = BackfillCacheManager("./my_cache")

# Load trades from cache
date = datetime(2025, 11, 7, tzinfo=timezone.utc)
trades = manager.load_cached_trades(date)

# Save trades to cache
manager.save_trades_to_cache(trades, date)

# Get cache file path
path = manager.get_cache_path(date)
print(f"Cache file: {path}")

# Get last cached timestamp
last_ts_ms = manager.get_last_cached_timestamp(trades)
print(f"Last trade timestamp: {last_ts_ms} ms")

# Deduplicate trades
deduped = manager.deduplicate_trades(trades)

# Clean old cache
manager.cleanup_old_cache(keep_days=7)
```

### Using Cache-Aware Backfill

```python
from app.context.backfill import BinanceTradeHistory
from app.ws.models import Settings
from datetime import datetime, timezone
import asyncio

async def backfill_with_cache():
    settings = Settings(
        backfill_cache_enabled=True,
        backfill_cache_dir="./my_cache"
    )
    history = BinanceTradeHistory(settings)
    
    # Use cache-aware backfill
    start = datetime(2025, 11, 7, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)
    
    trades = await history.backfill_with_cache(start, end)
    print(f"Backfilled {len(trades)} trades")

asyncio.run(backfill_with_cache())
```

## Troubleshooting

### Cache Not Being Used

**Symptom**: Logs show "Backfill: downloading trades" instead of "Backfill: using cache + resume"

**Solution**:
1. Check `BACKFILL_CACHE_ENABLED=true` in .env
2. Verify cache directory exists: `ls -la ./context_history_dir/backfill_cache/`
3. Check logs for "Cache not found" or permission errors

### Corrupt Cache File

**Symptom**: Error reading Parquet file on startup

**Solution**:
1. Delete the corrupt file: `rm ./context_history_dir/backfill_cache/backfill_YYYY-MM-DD.parquet`
2. Restart application - will re-download and recreate cache

### Large Cache Files (Disk Space)

**Symptom**: Cache files taking up significant disk space

**Solution**:
```python
# Keep only last 3 days
manager.cleanup_old_cache(keep_days=3)

# Or manually delete old files
rm ./context_history_dir/backfill_cache/backfill_2025-11-*.parquet  # older files
```

### Cache Not Updating

**Symptom**: Cache file timestamp is old, not getting updated on subsequent runs

**Cause**: May indicate an error during cache save
**Solution**:
1. Check logs for "Failed to save cache" errors
2. Verify directory is writable: `touch ./context_history_dir/backfill_cache/test`
3. Check disk space: `df -h`

## Acceptance Criteria

✅ Backfill cache saved to Parquet on first startup
✅ Second startup loads cache (no Binance download for old data)
✅ Only NEW hours downloaded on resume (e.g., 2-4h gap)
✅ Deduplication works (no duplicate trade IDs after merge)
✅ Performance: cache load < 100ms, full merge < 500ms
✅ Logs show cache hit/miss/resume clearly
✅ Config toggleable via .env
✅ Tests pass (cache write/read/merge/dedup)
✅ Rate limit pressure REDUCED dramatically (fewer requests on resume)
✅ Startup time: first run ~5-10min (full backfill), subsequent runs ~10-30s (cache + resume)

## Future Enhancements

1. **Cache versioning** - Handle schema changes across deployments
2. **Cache compression settings** - Configurable compression levels (speed vs size)
3. **Cache statistics** - Built-in cache hit ratio and performance metrics
4. **Incremental backfill** - Support for backfilling multiple previous days
5. **Remote cache** - Optional cloud storage backend (S3, GCS)
6. **Cache encryption** - Encrypt sensitive cached data
