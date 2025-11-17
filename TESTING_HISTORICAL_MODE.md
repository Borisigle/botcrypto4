# Testing Historical Mode - Quick Start Guide

## Quick Start: Verify VWAP/POCd Accuracy in 5 Minutes

### Step 1: Enable Historical Mode
Edit `.env` file:
```bash
CONTEXT_DISABLE_LIVE_DATA=true
CONTEXT_BACKFILL_ENABLED=true
```

### Step 2: Start Backend
```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

### Step 3: Monitor Logs
Look for backfill completion message:
```
✅ Backfill complete! TRADING NOW ENABLED
📊 Metrics are now PRECISE and ready for trading
📈 BACKFILL SUMMARY - Trades=45000 (backfill=45000, live=0, rejected=0)
```

**✅ If all trades are from backfill and 0 from live: PERFECT!**

### Step 4: Verify Trade Sources
```bash
curl http://localhost:8000/context/debug/trades | jq '.summary'
```

Expected output:
```json
{
  "total_trades": 45000,
  "trades_from_backfill": 45000,
  "trades_from_live": 0,
  "live_trades_rejected": 0,
  "backfill_percentage": 100.0,
  "live_percentage": 0.0
}
```

**✅ All trades from backfill = Data is pure!**

### Step 5: Verify Calculations
```bash
curl http://localhost:8000/context/debug/recalculate-verification | jq '.verification'
```

Expected output:
```json
{
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
}
```

**✅ Both match=true: Calculations are verified!**

### Step 6: Get Current Levels
```bash
curl http://localhost:8000/context/levels | jq
```

Output shows:
```json
{
  "OR": {
    "hi": 43200,
    "lo": 41500,
    "startTs": "2024-11-17T08:00:00+00:00",
    "endTs": "2024-11-17T08:10:00+00:00"
  },
  "VWAP": 42567.89,
  "VWAPprev": 42300.45,
  "PDH": 43600,
  "PDL": 40800,
  "VAHprev": 43200,
  "VALprev": 41500,
  "POCd": 42600,
  "POCprev": 42800
}
```

### Step 7: Compare with TradingView
1. Open TradingView chart: BTCUSDT or your symbol
2. Add "Volume Profile" indicator
3. Match the values:
   - POCd from API ←→ POC in TradingView
   - VAHprev ←→ Value Area High
   - VALprev ←→ Value Area Low
   - VWAPprev ←→ VWAP indicator

**Expected**: Values match exactly or within ±0.1%

---

## Detailed Testing Scenarios

### Scenario A: Pure Backfill Testing

**Goal**: Verify VWAP/POC from 100% historical data

**Setup**:
```bash
CONTEXT_DISABLE_LIVE_DATA=true
CONTEXT_BACKFILL_ENABLED=true
DATA_SOURCE=binance_ws  # or bybit for Bybit data
```

**Test Steps**:
1. Start backend at midnight UTC for full day backfill
2. Wait for backfill complete
3. Check `/debug/trades` → should show 100% backfill
4. Check `/debug/recalculate-verification` → should show all match=true
5. Compare values with external source

**Expected Result**: ✅ Perfect match with external source

---

### Scenario B: Live Data Disabled During Trading

**Goal**: Verify no live data is mixed in after backfill

**Setup**:
```bash
CONTEXT_DISABLE_LIVE_DATA=true
CONTEXT_BACKFILL_ENABLED=true
```

**Test Steps**:
1. Start backend mid-day (e.g., 10:00 UTC)
2. Wait for backfill to complete all trades since 00:00
3. Keep running for 1 hour with live trades coming in
4. Check `/debug/trades` after 1 hour

**Expected Result**:
```json
{
  "trades_from_backfill": 45000,
  "trades_from_live": 0,
  "live_trades_rejected": 1200  // ← live trades were coming but rejected
}
```

**✅ If `live_trades_rejected > 0`: Live data is being properly blocked!**

---

### Scenario C: Normal Operation (Live Data Enabled)

**Goal**: Verify normal operation still works with live data

**Setup**:
```bash
CONTEXT_DISABLE_LIVE_DATA=false  # Default
CONTEXT_BACKFILL_ENABLED=true
```

**Test Steps**:
1. Start backend
2. Wait for backfill complete
3. Keep running for 1 hour
4. Check `/debug/trades` after 1 hour

**Expected Result**:
```json
{
  "trades_from_backfill": 45000,
  "trades_from_live": 1200,        // ← Mix of sources
  "live_trades_rejected": 0
}
```

**✅ Both sources being used = Normal operation confirmed!**

---

### Scenario D: Previous Day Levels Verification

**Goal**: Verify previous day (VAH/VAL/POC/VWAP) is calculated correctly

**Setup**:
1. Run at start of new day (e.g., 00:05 UTC)
2. Enable backfill

**Check for in logs**:
```
📊 PREVIOUS DAY - PDH=43600.0, PDL=40800.0, 
    VAH=43200.0, VAL=41500.0, POC=42800.0, VWAP=42300.0
```

**Verify**:
1. These values should come from yesterday's 00:00-23:59 UTC trades
2. Should match yesterday's levels in TradingView

**✅ If values from previous day cache exist = Previous day calculation working!**

---

## Troubleshooting

### Issue: `trades_from_backfill` is 0
**Cause**: Backfill hasn't run yet or failed
**Fix**: 
- Check backfill logs for errors
- Verify `CONTEXT_BACKFILL_ENABLED=true`
- Check `/backfill/status` for progress

### Issue: `live_trades_rejected` never increases
**Cause**: No live trades coming in or backfill already complete
**Fix**:
- This is actually OK - means backfill was fast
- Once backfill complete and `CONTEXT_DISABLE_LIVE_DATA=true`, live trades are rejected
- Check that `/debug/trades` shows backfill complete

### Issue: VWAP/POC don't match external source
**Cause**: 
1. Different time period
2. Different tick size
3. Rounding differences
**Fix**:
1. Verify same symbol and date
2. Check `/debug/exchangeinfo` for tick size
3. Accept ±0.1% variance as acceptable
4. Check `/debug/recalculate-verification` shows match=true

### Issue: Previous day levels missing
**Cause**: Previous day cache not generated
**Fix**:
1. Run full day (00:00-23:59 UTC)
2. Previous day profile will be saved at day roll
3. Check file exists: `./data/history/BTCUSDT_YYYY-MM-DD_profile.parquet`

---

## Performance Monitoring

### Backfill Performance
Monitor log lines like:
```
Backfill progress: 10/100 chunks (10%), trades=4500 (backfill=4500), VWAP=42567.89, POC=42600.00
```

**Good signs**:
- Trade count increases steadily
- VWAP/POC values are stable (not jumping)
- Chunks progress from 0 to 100%

### Trade Ingestion Rate
From logs:
```
Backfill progress: 10000 trades loaded
Backfill progress: 20000 trades loaded
```

**Expected rates**:
- Public Binance API: 500-1000 trades/second
- Authenticated Binance: 2000-5000 trades/second
- Bybit API: 1000-3000 trades/second

### Data Integrity
After backfill completes, check:
```bash
curl http://localhost:8000/context/debug/recalculate-verification | jq '.data_integrity'
```

Expected:
```json
{
  "sum_price_qty_matches": true,
  "sum_qty_matches": true
}
```

**✅ Both true = Data integrity confirmed!**

---

## Comparison Workflow

### Compare VWAP Against TradingView

1. **Get API value**:
```bash
curl http://localhost:8000/context/levels | jq '.VWAP'
# Output: 42567.89
```

2. **Get TradingView value**:
   - Open chart: https://www.tradingview.com/chart/BTCUSDT/
   - Add "VWAP" indicator
   - Read value from chart

3. **Compare**:
   - API: 42567.89
   - TradingView: 42567.89
   - **✅ Match! Or within ±0.1%**

### Compare POC Against TradingView

1. **Get API value**:
```bash
curl http://localhost:8000/context/levels | jq '.POCd'
# Output: 42600.00
```

2. **Get TradingView value**:
   - Same chart, find "Volume Profile" indicator
   - Look for POC (marked with line)
   - Read price value

3. **Compare**:
   - API: 42600.00
   - TradingView: 42600.00
   - **✅ Match!**

### Compare VAH/VAL Against TradingView

1. **Get API values**:
```bash
curl http://localhost:8000/context/levels | jq '.VAHprev, .VALprev'
# Output: 43200, 41500
```

2. **Get TradingView values**:
   - Volume Profile indicator
   - VAH = upper bound of value area
   - VAL = lower bound of value area

3. **Compare**:
   - API VAH: 43200 ←→ TradingView: 43200 ✅
   - API VAL: 41500 ←→ TradingView: 41500 ✅

---

## Success Criteria

✅ **All of these must be true**:

1. Backfill completes with non-zero trade count
2. `/debug/trades` shows 100% from backfill (when disabled live data)
3. `/debug/recalculate-verification` shows all match=true
4. VWAP matches TradingView exactly
5. POC matches TradingView exactly
6. VAH/VAL match TradingView exactly (or within ±0.1%)
7. Previous day levels persisted and loaded on day roll
8. Logs show clear progression and completion

**If all 8 are true: VWAP/POC/VAH/VAL ARE 100% ACCURATE! ✅**

---

## Next Steps After Verification

1. **Enable Live Trading**: Set `CONTEXT_DISABLE_LIVE_DATA=false`
2. **Monitor Metrics**: Keep comparing with external sources
3. **Track Changes**: Watch how metrics evolve as trading progresses
4. **Validate Orders**: Ensure trading signals respect verified levels
5. **Compare Performance**: Track P&L against TradingView signals

---

**Questions?** Check `VWAP_POC_HISTORICAL_VERIFICATION_GUIDE.md` for detailed documentation.
