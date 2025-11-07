# Binance Backfill Circuit Breaker Guide

## Overview

The circuit breaker is an intelligent rate-limit management system that prevents backfill failures when Binance API imposes rate limits or bans. It tracks consecutive rate-limit errors (418/429/451 responses), implements automatic cooldowns, and dynamically adjusts concurrency and request delays.

## Features

### 1. Circuit Breaker States

The circuit breaker operates in three states:

- **CLOSED**: Normal operation. Requests proceed at configured concurrency and delay.
- **OPEN**: Rate limits detected. All requests are paused for a configured cooldown period.
- **HALF_OPEN**: After cooldown, attempting recovery by allowing requests to test if limits have been lifted.

### 2. Rate Limit Detection

Detects three types of rate limit responses from Binance:
- **418 (I'm a teapot)**: Bot detection/aggressive rate limiting
- **429 (Too Many Requests)**: Standard rate limiting
- **451 (Unavailable For Legal Reasons)**: Geographic or legal restrictions

### 3. Automatic Throttling

When rate limits are detected, the system automatically:
- Increases request delays (throttle multiplier)
- Reduces concurrent chunk downloads
- Logs state transitions for operator visibility

### 4. Fallback to Public Mode

When authenticated requests hit rate limits, the system automatically:
- Disables API key authentication
- Switches to public endpoints
- Re-queues failed chunks for retry

### 5. Progressive Recovery

After successful requests during HALF_OPEN state:
- Throttle multiplier gradually decreases
- Circuit breaker returns to CLOSED state
- Concurrency can be gradually restored

## Configuration

Add these to your `.env` file to customize circuit breaker behavior:

```env
# Rate limit threshold: number of consecutive errors to trigger breaker (default: 3)
BACKFILL_RATE_LIMIT_THRESHOLD=3

# Cooldown duration in seconds when breaker opens (default: 60)
BACKFILL_COOLDOWN_SECONDS=60

# Request delay in milliseconds for public endpoints (default: 100)
BACKFILL_PUBLIC_DELAY_MS=100
```

### Parameter Guide

**BACKFILL_RATE_LIMIT_THRESHOLD**
- **Type**: Integer
- **Default**: 3
- **Range**: 1-10 (recommended)
- **Purpose**: Number of consecutive 418/429/451 errors before opening circuit breaker
- **Tuning**: Lower values (1-2) for aggressive protection; higher values (5-10) for more tolerance

**BACKFILL_COOLDOWN_SECONDS**
- **Type**: Integer
- **Default**: 60
- **Range**: 10-300 (recommended)
- **Purpose**: Duration to wait before attempting recovery after opening circuit
- **Tuning**: Longer cooldowns (120-300s) if Binance consistently rate limits; shorter (10-30s) for temporary bursts

**BACKFILL_PUBLIC_DELAY_MS**
- **Type**: Integer (milliseconds)
- **Default**: 100
- **Range**: 50-500 (recommended)
- **Purpose**: Base delay between requests on public endpoints
- **Tuning**: Increase if still hitting rate limits; decrease if backfill is too slow

## Logging and Monitoring

### Key Log Messages

**Circuit breaker opening:**
```
ERROR Circuit breaker opened: 3 consecutive rate limit errors. 
      Enforcing 60s cooldown (throttle_multiplier=2.3x)
```

**Rate limit detected and fallback triggered:**
```
WARNING Rate limit detected on authenticated request (HTTP 429). 
        Downgrading to public mode (consecutive errors: 2)
```

**Recovery in progress:**
```
WARNING Circuit breaker open: cooldown active for 45.3s more
INFO    Circuit breaker: entering HALF_OPEN state to test recovery
```

**Recovery successful:**
```
INFO Circuit breaker: recovery successful, returning to CLOSED state
```

**Progress with throttling:**
```
INFO Progress: 20/72 chunks processed, ~100k trades, ~25s remaining 
     (throttle: 1.5x, concurrency: 10)
```

### Interpreting Throttle Multiplier

- **1.0x**: No throttling, requests at normal speed
- **1.5x**: Moderate throttling, slight slowdown
- **2.0-3.0x**: Heavy throttling, significant slowdown due to persistent rate limits
- **5.0x**: Maximum throttling, severe rate limit pressure

The multiplier affects:
- Request delays: `base_delay * throttle_multiplier`
- Concurrency reduction: Automatically halved at 1.5x, quartered at 2.0x+

## Behavioral Examples

### Example 1: Sudden Rate Limit Storm

**Scenario**: Authenticated mode hitting rate limits after 1000 chunks

```
1. First 418 error detected → throttle_multiplier = 1.5x, concurrent_errors = 1
2. Second 418 error → throttle_multiplier = 2.3x, concurrent_errors = 2
3. Third 418 error → throttle_multiplier = 3.4x, concurrent_errors = 3
   → Circuit breaker OPENS
   → Fallback to public mode
   → 60s cooldown enforced
   → All pending requests queued for retry

4. After 60s cooldown expires → Circuit enters HALF_OPEN
5. Test request succeeds → Circuit closes, throttle_multiplier gradually decreases
6. Backfill continues with reduced concurrency and increased delays
```

### Example 2: Graceful Degradation from Auth to Public

**Scenario**: API keys hit rate limits, graceful fallback

```
Initial state:
- Mode: Authenticated
- Concurrency: 20 concurrent chunks
- Delay: 0ms

After 429 error:
- Mode switches: Authenticated → Public
- Concurrency adjusted: 20 → 5 (per public mode settings)
- Delay increased: 0ms → 100ms (BACKFILL_PUBLIC_DELAY_MS)
- Log: "Downgrading to public mode (consecutive errors: 1)"

Result:
- Backfill slower but completes successfully
- ~8-10s (auth) → ~30-40s (public) runtime increase
- 100% chunk success rate instead of partial failures
```

### Example 3: Progressive Recovery

**Scenario**: After circuit closes, gradual improvement

```
After recovery (successful request):
- Circuit state: HALF_OPEN → CLOSED
- Error counter: 3 → 0
- Throttle multiplier: 5.0x → 4.75x (recovery begins)

Subsequent successful requests:
- Throttle 4.75x → 4.5x → 4.27x → ... → 1.0x (eventual recovery)
- Each successful request multiplies by 0.95
- Concurrency gradually restored as throttle decreases
- Progress log shows decreasing throttle: "throttle: 3.2x" → "throttle: 2.1x" → "throttle: 1.0"
```

## Troubleshooting

### Problem: "Circuit breaker repeatedly opens"

**Possible causes:**
- Binance API is temporarily experiencing issues
- API keys have rate limit restrictions
- Too many concurrent requests

**Solutions:**
1. Increase `BACKFILL_RATE_LIMIT_THRESHOLD` to 5-7 to tolerate occasional bursts
2. Increase `BACKFILL_COOLDOWN_SECONDS` to 120-300 for longer recovery window
3. Verify API key permissions and rate limits in Binance dashboard
4. Check if another process is also using the same API keys

### Problem: "Backfill taking too long after fallback to public mode"

**Expected behavior:**
- Public mode: ~30-40 seconds for 12-hour backfill (5 concurrent, 100ms delay)
- Auth mode: ~8-10 seconds for same window (20 concurrent, 0ms delay)

**If still too slow:**
1. Reduce `BACKFILL_PUBLIC_DELAY_MS` to 50ms (be cautious, may trigger rate limits)
2. Ensure no other processes are using API or network

### Problem: "429 errors continue even after public fallback"

**Possible causes:**
- IP-level rate limits from Binance
- Network congestion
- Genuine high request volume

**Solutions:**
1. Increase `BACKFILL_PUBLIC_DELAY_MS` to 200-500ms
2. Increase `BACKFILL_COOLDOWN_SECONDS` to 180-300s
3. Run backfill during off-peak hours
4. Contact Binance support about rate limit status

## Performance Expectations

### With Circuit Breaker Enabled (Normal Operation)

| Time Window | Auth Mode | Public Mode | Success Rate |
|------------|-----------|-------------|--------------|
| 1 hour     | 0.5-1s    | 3-4s        | 100%         |
| 6 hours    | 2-3s      | 12-15s      | 100%         |
| 12 hours   | 8-10s     | 30-40s      | 100%         |
| 24 hours   | 15-20s    | 60-80s      | 100%         |

### Under Rate Limit Pressure

With circuit breaker active:
- Temporary slowdown but eventual completion
- Zero unhandled exceptions
- Clear logging of state transitions
- Automatic recovery when pressure subsides

## Best Practices

1. **Monitor logs during backfill**: Watch for "Circuit breaker opened" messages
2. **Set reasonable thresholds**: Start with defaults, adjust based on your API limits
3. **Use authentication when possible**: 20-40x faster than public endpoints
4. **Run backfill off-peak**: Reduce likelihood of hitting global rate limits
5. **Maintain API key security**: Avoid sharing keys, use separate keys for different services
6. **Test with small windows first**: Before running 24-hour backfills, test with 1-hour windows

## Advanced: Custom Tuning Example

For a production setup with strict rate limits:

```env
# Conservative settings for aggressive rate limiting environment
BACKFILL_RATE_LIMIT_THRESHOLD=2      # Trigger breaker quickly
BACKFILL_COOLDOWN_SECONDS=180        # Long recovery window (3 minutes)
BACKFILL_PUBLIC_DELAY_MS=200         # Slow down public requests
BACKFILL_MAX_RETRIES=10              # More retry attempts
BACKFILL_RETRY_BASE=1.0              # Longer initial backoff
```

This configuration prioritizes stability over speed, ideal for environments with:
- Shared Binance API quotas
- Strict rate limit policies
- Production-critical backfill requirements

## See Also

- `BACKfill_FIX_SUMMARY.md` - Previous backfill optimizations
- `HMAC_TEST_MODE_README.md` - API authentication testing
- Backend code: `backend/app/context/backfill.py` - Implementation details
