# Bybit Connector Fix - Testing Checklist

## Pre-Deployment Verification ✅

### Code Quality
- [x] All 27 Bybit connector tests passing
- [x] All 26 HFT connector tests passing  
- [x] Python syntax validated
- [x] No import errors
- [x] Type hints preserved

### Key Features Verified
- [x] BybitConnector instantiation works
- [x] BybitConnectorRunner instantiation works
- [x] Subprocess script generation works
- [x] Stderr monitoring attribute present
- [x] Stale connection detection configured (60s)
- [x] Health check loop implemented
- [x] Reconnection logic in place

## Post-Deployment Testing

### Phase 1: Initial Connection (First 5 minutes)
- [ ] Backend starts without errors
- [ ] Connector status shows "Connected"
- [ ] See log: `bybit_connector_subprocess_connected`
- [ ] See log: `bybit_connector_connected`
- [ ] Live data starts flowing
- [ ] Price updates in real-time
- [ ] No errors in stderr logs

### Phase 2: Steady State (30 minutes)
- [ ] Continuous data flow
- [ ] Health check logs every 60s showing:
  - `process_alive: true`
  - `queue_size` reasonable (<100)
  - `error_count: 0` or very low
  - `seconds_since_last_event` < 5s
- [ ] No unexpected disconnections
- [ ] No stale connection warnings

### Phase 3: Resilience Testing (Optional)

#### Test A: Process Kill
1. Find subprocess PID from logs
2. Kill process: `kill -9 <PID>`
3. Expected:
   - [ ] Log: `bybit_connector_process_died`
   - [ ] Log: `connector_disconnected`
   - [ ] Automatic reconnection attempt
   - [ ] Log: `bybit_connector_connected`
   - [ ] Data flow resumes within 10-30s

#### Test B: Long Running (24 hours)
- [ ] No memory leaks
- [ ] No accumulated errors
- [ ] Consistent performance
- [ ] No zombie processes

## Monitoring Metrics

### Normal Operation Ranges
- `seconds_since_last_event`: 0-5s (during active trading)
- `error_count`: 0-5 over 24h
- `queue_size`: 0-50 typically
- `reconnection_attempts`: 0-2 per day

### Alert Thresholds
- ⚠️ `seconds_since_last_event` > 30s (warning)
- 🚨 `seconds_since_last_event` > 60s (critical - triggers reconnect)
- ⚠️ `error_count` > 10 (warning)
- 🚨 `error_count` > 50 (critical)
- ⚠️ Reconnections > 5/hour (warning)

## Key Log Events to Monitor

### Success Events
```json
{"event": "bybit_connector_subprocess_connected"}
{"event": "bybit_connector_connected", "symbol": "BTCUSDT"}
{"event": "bybit_connector_health_check", "process_alive": true, ...}
```

### Warning Events
```json
{"event": "bybit_connector_subprocess_stderr", "stderr": "..."}
{"event": "connector_connection_error", "attempt": 1, ...}
```

### Critical Events
```json
{"event": "bybit_connector_process_died"}
{"event": "bybit_connector_stale_connection_detected", "seconds_since_last_event": 62}
```

## Rollback Plan

If issues occur:

1. **Immediate**: Check logs for specific error messages
2. **Quick Fix**: Restart backend service (will trigger clean reconnect)
3. **Rollback**: Revert to previous version if problems persist

Git branch: `fix/hft-bybit-connector-disconnect-reconnect-logs`

## Success Criteria

The fix is considered successful if:

1. ✅ Connector maintains stable connection for >24 hours
2. ✅ Automatic reconnection works when process dies
3. ✅ Stale connection detection triggers at 60s threshold
4. ✅ Health logs provide useful debugging information
5. ✅ Stderr logs help diagnose library-level issues
6. ✅ No increase in CPU/memory usage
7. ✅ Trade/depth data flows continuously

## Documentation

Created files:
- `BYBIT_CONNECTOR_FIX_SUMMARY.md` - Technical details (English)
- `SOLUCION_BYBIT_CONNECTOR.md` - Solution summary (Spanish)
- `TESTING_CHECKLIST.md` - This file

Modified files:
- `backend/app/data_sources/bybit_connector.py` (+184 lines)

## Support Information

If issues are encountered:

1. Check logs for structured events (grep for "bybit_connector")
2. Look for stderr output: `bybit_connector_subprocess_stderr`
3. Check health metrics: `bybit_connector_health_check`
4. Verify subprocess is running: `ps aux | grep python`
5. Review reconnection attempts in logs

## Next Steps After Verification

1. Monitor production for 24-48 hours
2. Collect metrics on reconnection frequency
3. Fine-tune stale detection threshold if needed (currently 60s)
4. Document any edge cases discovered
5. Consider adding Grafana dashboard for monitoring
