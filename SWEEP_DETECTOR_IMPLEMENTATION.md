# Sweep Detector + Strategy Engine Implementation

## Overview
The Sweep Detector is a confluence-based trading signal generator that detects setups by analyzing:
1. **CVD Divergence**: Price decreasing while Cumulative Volume Delta increases (bullish signal)
2. **Volume Delta Spike**: Sudden increase in volume delta (1.5x+ average)
3. **Liquidation Clusters** (optional): Support/resistance from liquidation price levels

When all conditions are met, a trading **Signal** is generated with entry, stop loss, take profit, and risk/reward ratio.

## Components Implemented

### 1. Signal Model (`backend/app/models/indicators.py`)
Added the `Signal` class representing a trading signal:

```python
class Signal(BaseModel):
    timestamp: datetime
    setup_type: str  # "bullish_sweep" or "bearish_sweep"
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float  # TP - Entry / Entry - SL
    confluence_score: float  # 0-100 (setup strength)
    
    # Indicator details
    cvd_value: float
    cvd_divergence: bool
    volume_delta: float
    volume_delta_percentile: float  # vs historical
    liquidation_support: Optional[float]
    liquidation_resistance: Optional[float]
    reason: str  # Explanation
```

### 2. SweepDetector Service (`backend/app/services/sweep_detector.py`)
Main analysis engine with the following features:

#### Key Methods:
- `analyze()`: Main analysis method that checks confluence and generates signals
  - Requires: current price, CVD snapshot, volume delta snapshot
  - Optional: liquidation support/resistance
  - Returns: Signal object or None

- `_detect_cvd_divergence()`: Detects bullish divergence (price down, CVD up)
  - Requires 20+ history samples
  - Compares last value with 10 samples ago

- `_detect_volume_delta_spike()`: Detects sudden volume pressure
  - Requires 20+ history samples
  - Triggers when current > 1.5x average

- `_generate_signal()`: Creates complete signal with TP/SL calculations
  - Entry: Current price
  - SL: Liquidation support (if available) or -1% from entry
  - TP: Liquidation resistance (if available) or +3% from entry
  - Score: Based on confluence (base 50 + indicators)

#### Thread Safety:
- Uses `Lock` for concurrent access to history and signals
- Stores up to 100 recent signals (deque with maxlen=100)
- Stores up to 1000 CVD and Volume Delta samples

#### Global Access:
- `init_sweep_detector()`: Initialize the singleton
- `get_sweep_detector()`: Get the initialized instance

### 3. Signals Router (`backend/app/routers/signals.py`)
FastAPI router with three endpoints:

#### `GET /signals/current`
Returns the last generated signal or null.

**Response:**
```json
{
  "timestamp": "2025-11-20T01:37:09.938324+00:00",
  "setup_type": "bullish_sweep",
  "entry_price": 98.0,
  "stop_loss": 96.515,
  "take_profit": 99.495,
  "risk_reward": 1.01,
  "confluence_score": 100.0,
  "cvd_value": 2000.0,
  "cvd_divergence": true,
  "volume_delta": 50.0,
  "volume_delta_percentile": 75.0,
  "liquidation_support": 97.0,
  "liquidation_resistance": 99.0,
  "reason": "CVD divergence + Volume Delta spike + Liquidation support at 97.00"
}
```

#### `GET /signals/history?limit=50`
Returns array of recent signals (max 100).

**Query Parameters:**
- `limit` (int, 1-100, default 50): Number of signals to return

**Response:**
```json
[
  { "...signal 1..." },
  { "...signal 2..." },
  { "...signal N..." }
]
```

#### `POST /signals/analyze`
Manually trigger analysis on current market data.

**Response:**
Same as `/signals/current` (returns signal or null)

### 4. Background Analysis Task (main.py)
Continuous signal detection every 5 seconds:

```python
async def _sweep_detector_loop():
    # Runs every 5 seconds
    # Collects current price, CVD, volume delta, liquidation levels
    # Calls sweep_detector.analyze()
    # Generates signals automatically
```

This loop:
- Runs every 5 seconds
- Gets current trades and price
- Calculates CVD and Volume Delta
- Retrieves liquidation support/resistance
- Analyzes setup and generates signals
- Handles errors gracefully

### 5. Integration in main.py
- Initializes `SweepDetector` at startup
- Starts background analysis task
- Registers signals router
- Cleanly shuts down task at shutdown

## Confluence Scoring Logic

Signals receive a confidence score (0-100) based on:
- **Base Score**: 50
- **CVD Divergence**: +25 (if detected)
- **Volume Delta Spike**: +25 (if detected)
- **Liquidation Levels**: +10 (if price is between support/resistance)
- **Cap**: Maximum 100

### Example Scores:
- Both signals + liquidations: 100 (strongest)
- Both signals only: 90
- One signal only: 65-75
- No signals: No signal generated (filtered out)

## Trade Parameters

### Entry Price
- Current market price at time of signal

### Stop Loss Calculation
- **With Liquidations**: Support × 0.995 (0.5% below)
- **Without Liquidations**: Entry × 0.99 (1% below)

### Take Profit Calculation
- **With Liquidations**: Resistance × 1.005 (0.5% above)
- **Without Liquidations**: Entry × 1.03 (3% above)

### Risk/Reward Ratio
```
RR = (TP - Entry) / (Entry - SL)
```

## Historical Data Storage

### Signal History
- Deque with `maxlen=100`
- Automatically evicts oldest when full
- Accessed via `get_signals_history(limit)`

### CVD History
- Deque with `maxlen=1000`
- Stores: time, CVD value, price
- Used for divergence detection

### Volume Delta History
- Deque with `maxlen=1000`
- Stores: time, volume_delta value
- Used for spike detection

## Error Handling

- All endpoints return 500 errors with descriptive messages
- Background task logs errors but continues running
- Missing data gracefully skipped (no price, no analysis)
- Thread-safe with Lock protection

## Logging

Logs are output with `sweep_detector` logger at INFO level:
- Signal generation: `"SIGNAL GENERATED: {type} at {price}, RR: {rr}, Score: {score}"`
- Background task start: `"Sweep detector analysis loop started (interval=5s)"`
- Errors: Full exception traces

## Testing

### Unit Tests (test_sweep_detector.py)
- 11 test cases covering:
  - Initialization
  - CVD divergence detection
  - Volume delta spike detection
  - Signal generation
  - No signal without conditions
  - Signal history retrieval
  - Model serialization
  - Volume delta percentile calculation

### Integration Tests (test_acceptance_criteria.py)
Validates all acceptance criteria:
- CVD divergence detection ✓
- Volume Delta spike detection ✓
- Signal generation with RR ✓
- GET /signals/current endpoint ✓
- GET /signals/history endpoint ✓
- Confluence score (0-100) ✓
- Signal logging ✓
- Historical limit (100) ✓
- Complete Signal model ✓
- Liquidation support/resistance ✓

## API Usage Examples

### Get Current Signal
```bash
curl http://localhost:8000/signals/current | jq
```

### Get Signal History (last 10)
```bash
curl "http://localhost:8000/signals/history?limit=10" | jq
```

### Manually Trigger Analysis
```bash
curl -X POST http://localhost:8000/signals/analyze | jq
```

## Dependencies

- FastAPI (routing)
- Pydantic (models)
- Python async/await
- Collections (deque)
- Threading (Lock)
- Logging

## Performance Considerations

- **Memory**: ~100KB for 100 signals + 2000 history samples
- **CPU**: Background task runs every 5 seconds (minimal overhead)
- **Thread Safety**: Lock-based synchronization (fast)
- **No External Calls**: Uses in-memory data from existing services

## Future Enhancements

Possible improvements:
1. Bearish sweep detection (CVD down, price up)
2. Configurable confluence thresholds
3. Multiple timeframe analysis
4. Signal filtering based on volatility
5. Performance metrics tracking (hit rate, RR average)
6. Signal modification/cancellation
7. Trade execution integration
