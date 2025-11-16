# Bybit HFT Connector Disconnect/Reconnect Fix

## Problem Summary
The Bybit HFT Connector was experiencing disconnections where:
- Connector status showed "Down" / "Disconnected"
- Live data stopped flowing after initial connection
- Last update timestamp was frozen
- Backfill worked correctly, indicating initial connection was OK
- No automatic reconnection was happening

## Root Causes Identified

1. **No Automatic Reconnection**: When the subprocess died or disconnected, the `_check_connection_loop` only detected and logged the issue but didn't trigger reconnection logic.

2. **Stale Connection Not Detected**: If the connection appeared alive but stopped receiving events, there was no mechanism to detect this "stale" state.

3. **Missing Error Logs**: Subprocess stderr was not being monitored, so important error messages from the hftbacktest library were lost.

4. **No Subprocess Exit Code Monitoring**: When the subprocess exited, the exit code was not being logged, making diagnosis difficult.

5. **Incomplete Cleanup on Reconnect**: When reconnecting, old subprocess and tasks weren't properly cleaned up before starting a new connection.

## Fixes Implemented

### 1. Enhanced Subprocess Monitoring (`BybitConnectorRunner`)

#### Added stderr monitoring:
```python
self._stderr_task: Optional[asyncio.Task] = None
```
- New `_read_stderr()` method captures and logs all stderr output from subprocess
- Helps diagnose connection issues from hftbacktest library

#### Improved stdout reading:
- Detects subprocess exit and logs exit codes
- Better handling of empty lines and EOF conditions
- More detailed JSON decode error logging

#### Better cleanup on stop:
- Cancels both stdout and stderr reading tasks
- Ensures proper SIGTERM/SIGKILL handling
- Cleans up all resources

### 2. Stale Connection Detection (`BybitConnector`)

#### New tracking:
```python
self._stale_connection_seconds = 60  # Consider stale if no events for 60s
```

#### Enhanced `_check_connection_loop`:
- Monitors time since last event received
- Detects "stale" connections (appears connected but no data flowing)
- Waits 30 seconds after connection before checking to avoid false positives
- Logs periodic health status every 60 seconds with:
  - Process alive status
  - Queue size
  - Error count
  - Time since last event

#### Automatic disconnection trigger:
When stale connection detected, sets `self._connected = False` which triggers:
1. `is_connected()` returns False
2. `HFTConnectorStream._ensure_connected()` detects disconnection
3. Automatic reconnection attempt is initiated

### 3. Improved Reconnection Logic

#### Enhanced `connect()` method:
- Checks if already connected with alive process
- Properly cleans up old runner before creating new one
- Resets subscription state on reconnect
- Cancels old health check task before starting new one
- Better error handling and logging

#### Key sequence on reconnect:
1. Stop old runner if exists
2. Create new runner
3. Start subprocess
4. Reset subscribed_trades and subscribed_depth flags
5. Re-subscribe to channels (handled by HFTConnectorStream)
6. Start new health check loop

### 4. Enhanced Subprocess Script

#### Added comprehensive logging:
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stderr
)
```

#### Better error handling:
- Logs all exceptions with type and message
- Continues on non-fatal errors instead of exiting
- Tracks and logs event count every 60 seconds
- Graceful shutdown with cleanup

#### Improved event loop:
- Catches and logs individual event processing errors
- Doesn't exit on single event error
- Uses small sleep after error before continuing

## Logging Improvements

### New structured log events:

1. **bybit_connector_subprocess_connected** - Subprocess successfully connected
2. **bybit_connector_subprocess_exited** - Subprocess exited with code
3. **bybit_connector_subprocess_terminated** - Subprocess terminated
4. **bybit_connector_subprocess_stderr** - Error output from subprocess
5. **bybit_connector_json_decode_error** - Failed to parse JSON from subprocess
6. **bybit_connector_subprocess_error** - Error event from subprocess
7. **bybit_connector_process_died** - Health check detected dead process
8. **bybit_connector_stale_connection_detected** - No events for >60s
9. **bybit_connector_health_check** - Periodic health status (every 60s)
10. **bybit_connector_cleanup_error** - Error during old runner cleanup
11. **bybit_connector_stderr_read_error** - Error reading stderr

### Subprocess logs (to stderr):
- Connection initialization progress
- Subscription confirmation
- Event processing statistics (every 60s)
- All errors with full context

## Expected Behavior After Fix

1. **Initial Connection**: Works as before, connects and starts receiving events

2. **Process Death**: 
   - Detected within 5 seconds by health check loop
   - Logs: `bybit_connector_process_died`
   - `is_connected()` returns False
   - HFTConnectorStream triggers reconnection
   - New subprocess started automatically

3. **Stale Connection**:
   - If no events received for 60 seconds
   - Logs: `bybit_connector_stale_connection_detected`
   - Connection marked as disconnected
   - Automatic reconnection initiated

4. **Subprocess Errors**:
   - Captured from stderr and logged
   - Helps diagnose library-level issues
   - Visible in application logs

5. **Periodic Health Logs**:
   - Every 60 seconds
   - Shows connection status, event count, errors
   - Helps monitor ongoing health

## Testing Recommendations

1. **Normal Operation**: Verify live data flows continuously and logs show regular events

2. **Manual Process Kill**: Kill subprocess PID and verify automatic reconnection

3. **Network Issues**: Simulate network interruption and verify detection + reconnection

4. **Stale Connection**: Block events (if possible) and verify 60s detection timeout

5. **Long Running**: Monitor logs over hours/days for any anomalies

## Configuration

No configuration changes needed. The fix uses sensible defaults:
- Stale connection threshold: 60 seconds
- Health check interval: 5 seconds  
- Health log interval: 60 seconds
- Startup grace period: 30 seconds (before stale detection)

## Monitoring

Key metrics to watch in logs:
- `seconds_since_last_event` - Should stay low (<5s) during active trading
- `error_count` - Should remain 0 or very low
- `queue_size` - Should stay reasonable (<100)
- Reconnection attempts - Should be rare in stable network

## Files Modified

1. `backend/app/data_sources/bybit_connector.py`
   - Enhanced BybitConnectorRunner class
   - Enhanced BybitConnector class
   - Improved subprocess script
   - Added stale connection detection
   - Better logging throughout
