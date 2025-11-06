# HMAC Authentication Test Mode

This hotfix implements a test mode for validating Binance HMAC-SHA256 API authentication before expanding to full backfill operations.

## Purpose

Test API key authentication with minimal scope:
- Fetch only 1 window (10 minutes) serially 
- Verify HMAC signing is correct
- Provide detailed logging for debugging
- Expand to full backfill only if successful

## Usage

### 1. Test Mode with API Keys

```bash
# Set your Binance API credentials
export BINANCE_API_KEY=your_api_key_here
export BINANCE_API_SECRET=your_api_secret_here

# Enable test mode
export CONTEXT_BACKFILL_TEST_MODE=true

# Run the test
python test_hmac_auth.py
```

### 2. Test Mode without API Keys (Public Endpoints)

```bash
# Ensure no API credentials are set
unset BINANCE_API_KEY
unset BINANCE_API_SECRET

# Enable test mode
export CONTEXT_BACKFILL_TEST_MODE=true

# Run the test
python test_hmac_auth.py
```

### 3. Production Mode (Full Backfill)

```bash
# After successful test, disable test mode
export CONTEXT_BACKFILL_TEST_MODE=false

# Start the application normally
# The backfill will now run with full parallelization
```

## Expected Output

### Successful Test with API Keys

```
=== HMAC AUTHENTICATION TEST ===
Symbol: BTCUSDT
REST Base URL: https://fapi.binance.com
Test Mode: True
API Key: abcd...efgh
API Secret: ********************

=== HMAC AUTHENTICATION TEST MODE ===
Test mode: fetching single 1-hour window
  Window: 2025-11-06T00:00:00+00:00 to 2025-11-06T01:00:00+00:00
  Timestamp: 1762387200000 - 1762390800000
  Signature preview: a1b2c3d4e5f6789012345678901234...

HTTP Request: GET https://fapi.binance.com/fapi/v1/aggTrades
  Params: symbol=BTCUSDT, startTime=1762387200000, endTime=1762390800000, limit=1000
  Auth: timestamp=1736179200000, recvWindow=5000
  Signature: a1b2c3d4e5f6789012345678901234...
HTTP Response: 200 OK

Test result: 8,432 trades loaded from test window
VWAP (partial): 103456.78
POCd (partial): 103450.00
✅ Success! HMAC authentication working correctly
Ready to expand to full backfill...

✅ Test completed successfully!
   Trades loaded: 8432
   Price range: $103200.00 - $103700.00
   Average price: $103456.78
   Total volume: 84.320000 BTC

🎉 HMAC authentication is working correctly!
   Ready to expand to full backfill (set CONTEXT_BACKFILL_TEST_MODE=false)
```

### Test without API Keys

```
=== HMAC AUTHENTICATION TEST ===
Symbol: BTCUSDT
REST Base URL: https://fapi.binance.com
Test Mode: True
WARNING: No API credentials configured - using public endpoints

=== HMAC AUTHENTICATION TEST MODE ===
Test mode: fetching single 1-hour window
  Window: 2025-11-06T00:00:00+00:00 to 2025-11-06T01:00:00+00:00
  Timestamp: 1762387200000 - 1762390800000
  WARNING: No API credentials configured, using public endpoints
```

## Error Handling

### 401 Unauthorized
```
❌ Test failed: Binance API authentication failed (401). Check your API credentials.
Check your API credentials and network connection
For API key issues, verify:
  - BINANCE_API_KEY is correct
  - BINANCE_API_SECRET is correct
  - API key has 'Read' permissions for futures trading
```

### 418 Bot Detection
```
❌ Test failed: HTTP 418 error in test mode!
Response headers: {...}
Response body: {"code": -1003, "msg": "Too much request weight used; current limit is 1200 request weight per 1 MINUTE. Please use the websocket for live data to avoid polling the API."}
Request URL: https://fapi.binance.com/fapi/v1/aggTrades
Request params: {...}
```

## Implementation Details

### Test Window
- **Time**: 2025-11-06T00:00:00 to 2025-11-06T01:00:00 UTC
- **Duration**: 1 hour (should be ~5-10k trades for BTCUSDT)
- **Timestamps**: 1762387200000 to 1762390800000

### Configuration Changes
- **Serial Execution**: `max_concurrent_chunks = 1`
- **No Delays**: `request_delay = 0.0`
- **Enhanced Logging**: Full request/response details in test mode
- **Early Exit**: Test completes after single window, no full backfill

### HMAC Signature Verification
The test validates that:
1. Parameters are correctly sorted and encoded
2. HMAC-SHA256 signature is properly generated
3. API key is included in X-MBX-APIKEY header
4. Timestamp and recvWindow are properly added
5. Signature matches Binance server expectations

## Next Steps After Successful Test

1. **Disable test mode**: `export CONTEXT_BACKFILL_TEST_MODE=false`
2. **Restart application**: Full backfill will run with normal parallelization
3. **Monitor logs**: Look for "Backfill complete" messages with trade counts
4. **Performance expectations**:
   - **With API keys**: ~8-10 seconds (20 parallel, no delays)
   - **Without API keys**: ~30-40 seconds (5 parallel, throttled)

## Troubleshooting

### Common Issues

1. **Invalid API Keys**
   - Verify keys are copied correctly (no extra spaces)
   - Ensure API key has "Enable Reading" permission
   - Check if IP restrictions are configured

2. **Timestamp Issues**
   - Ensure system time is synchronized
   - Check for timezone differences
   - Verify NTP is running if on server

3. **Rate Limits**
   - Public endpoints: 1200 requests per minute
   - Authenticated endpoints: Higher limits vary by tier
   - Use test mode to avoid hitting limits during development

### Debug Mode

For additional debugging, set:
```bash
export LOG_LEVEL=DEBUG
```

This will show:
- Full HTTP request parameters
- Complete response headers
- HMAC signature details
- Detailed error responses