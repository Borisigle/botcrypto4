# Strategy Framework

This directory contains the trading strategy framework that provides real-time market analysis, regime detection, and signal generation for the botcrypto4 trading system.

## Architecture Overview

The strategy framework is built around several key components:

### Core Components

1. **StrategyEngine** (`engine.py`) - Main orchestrator that:
   - Subscribes to real-time trade data from WSModule
   - Aggregates trades into configurable candles (1m, 5m)
   - Manages component lifecycle and event routing
   - Exposes pub/sub interface for analyzers

2. **SessionScheduler** (`scheduler.py`) - Manages trading sessions:
   - London session: 08:00-12:00 UTC
   - NY overlap session: 13:00-17:00 UTC
   - Provides enable/disable signals for strategy components
   - Emits session change events

3. **ContextAnalyzer** (`analyzers/context.py`) - Market regime detection:
   - Ingests VWAP, POC, cumulative delta, and volume profile from ContextService
   - Detects premarket range vs trend regimes
   - Emits structured diagnostics for scoring

4. **Models** (`models.py`) - Data structures:
   - Candle data, session states, market regimes
   - Strategy events and signals
   - API response models

## Session Management

The framework follows strict trading session windows:

### London Session
- **Time**: 08:00-12:00 UTC
- **Characteristics**: Typically range-bound trading
- **Strategy Focus**: Mean reversion, level trading

### NY Overlap Session  
- **Time**: 13:00-17:00 UTC
- **Characteristics**: Higher volatility, potential trends
- **Strategy Focus**: Momentum, breakout trading

### Session State Transitions
```
OFF → LONDON (08:00 UTC)
LONDON → OFF (12:00 UTC)  
OFF → OVERLAP (13:00 UTC)
OVERLAP → OFF (17:00 UTC)
```

## Candle Aggregation

The engine maintains real-time candle buffers for multiple timeframes:

### Supported Timeframes
- **1m**: One-minute candles for high-frequency analysis
- **5m**: Five-minute candles for medium-term patterns

### Aggregation Logic
```python
# Trades are aggregated into OHLCV candles
# Each candle includes:
- open: First trade price
- high: Maximum trade price  
- low: Minimum trade price
- close: Last trade price
- volume: Total trade volume
- trades: Number of trades
```

## Market Regime Detection

The ContextAnalyzer classifies market conditions using multiple factors:

### Range Regime Characteristics
- VWAP and POC are close together
- Low cumulative delta
- High volume concentration around specific levels
- Typical during London session

### Trend Regime Characteristics  
- VWAP and POC are far apart
- Strong cumulative delta (buying/selling pressure)
- Dispersed volume profile
- More common during overlap session

### Classification Algorithm
```python
# Multiple factors are weighted:
1. VWAP-POC distance (30% weight)
2. Cumulative delta strength (30% weight) 
3. Volume distribution (20% weight)
4. Session context (20% weight)

# Final classification uses threshold logic
trend_score > range_score + threshold → TREND
else → RANGE
```

## API Endpoints

### `/strategy/status`
Returns comprehensive strategy status:
```json
{
  "engine_state": {
    "is_running": true,
    "current_session": "london",
    "active_timeframes": ["1m", "5m"],
    "candle_buffers": {...}
  },
  "context_analysis": {
    "regime": "range",
    "confidence": 0.75,
    "vwap": 100.50,
    "poc": 100.45,
    "cumulative_delta": 25.3
  },
  "scheduler_state": {
    "current_session": "london",
    "time_to_change_seconds": 7200
  }
}
```

### `/strategy/candles?timeframe=1m&count=100`
Returns recent candle data:
```json
{
  "timeframe": "1m",
  "count": 50,
  "candles": [
    {
      "timestamp": "2024-01-15T10:00:00Z",
      "open": 100.00,
      "high": 100.50,
      "low": 99.75,
      "close": 100.25,
      "volume": 125.5,
      "trades": 45
    }
  ]
}
```

### `/strategy/analysis/diagnostics`
Returns detailed analysis diagnostics:
```json
{
  "analysis": {...},
  "context": {...},
  "levels": {...},
  "volume_profile": {...},
  "parameters": {...}
}
```

## Event System

The framework uses a pub/sub event system for component communication:

### Event Types
- `candle_complete`: Emitted when a candle closes
- `session_change`: Emitted on session transitions
- `regime_change`: Emitted on regime detection changes

### Subscribing to Events
```python
def handle_candle_complete(event):
    candle = event.data["candle"]
    timeframe = event.data["timeframe"]
    # Process candle...

engine.subscribe_events("candle_complete", handle_candle_complete)
```

## Configuration

The framework inherits configuration from the main application settings:

### Key Settings
- `SYMBOL`: Trading symbol (default: BTCUSDT)
- `LOG_LEVEL`: Logging level (default: INFO)

### Session Timing
Session windows are hardcoded to match forex market hours:
- London: 08:00-12:00 UTC
- NY Overlap: 13:00-17:00 UTC

## Integration with Existing Services

### Context Service Integration
- Consumes VWAP, POC, volume profile data
- Uses cumulative delta for regime analysis
- Leverages existing session state logic

### WebSocket Module Integration  
- Receives real-time trade data
- Forwards trades to strategy engine
- Maintains existing metrics and health monitoring

### Application Lifecycle
- Starts up with main application
- Shuts down gracefully with other services
- Shares logging configuration

## Testing

The framework includes comprehensive tests:

### Test Coverage
- Session scheduler timing and boundaries
- Candle aggregation accuracy
- Regime classification logic
- Event system functionality
- Integration scenarios

### Running Tests
```bash
# Run all strategy tests
pytest backend/app/tests/test_strategy_engine.py -v

# Run specific test class
pytest backend/app/tests/test_strategy_engine.py::TestSessionScheduler -v
```

## Usage Examples

### Basic Strategy Component
```python
from app.strategy.engine import get_strategy_engine
from app.strategy.analyzers.context import get_context_analyzer

# Get engine instance
engine = get_strategy_engine()

# Subscribe to candle events
def on_candle(event):
    candle = event.data["candle"]
    print(f"New {event.data['timeframe']} candle: {candle.close}")

engine.subscribe_events("candle_complete", on_candle)

# Get current analysis
analyzer = get_context_analyzer()
analysis = analyzer.analyze()
print(f"Current regime: {analysis.regime}")
```

### Custom Analyzer
```python
from app.strategy.models import StrategyEvent

class CustomAnalyzer:
    def __init__(self, engine):
        self.engine = engine
        engine.subscribe_events("candle_complete", self.on_candle)
    
    def on_candle(self, event):
        candle = event.data["candle"]
        # Custom analysis logic
        if self.detect_signal(candle):
            self.emit_signal(candle)
```

## Performance Considerations

### Memory Management
- Candle buffers are limited to 1000 candles per timeframe
- Event subscribers are weakly referenced
- Periodic cleanup of old data

### CPU Usage
- Candle aggregation runs every second
- Regime analysis is on-demand
- Session monitoring uses efficient time checks

### Network Efficiency
- Reuses existing WebSocket connections
- No additional API calls required
- Minimal data overhead

## Future Enhancements

### Planned Features
- Additional timeframes (15m, 1h)
- Signal generation modules
- Backtesting framework
- Performance analytics
- Strategy parameter optimization

### Extension Points
- Custom analyzer plugins
- Additional data sources
- Machine learning integration
- Risk management modules

## Troubleshooting

### Common Issues

1. **No candles generated**
   - Check if session is active
   - Verify trade data is flowing
   - Check timezone settings

2. **Incorrect regime detection**
   - Verify context service data quality
   - Check volume thresholds
   - Review analysis parameters

3. **Session timing issues**
   - Confirm system timezone is UTC
   - Check for clock drift
   - Verify session boundary logic

### Debug Logging
Enable debug logging for detailed insights:
```python
import logging
logging.getLogger("strategy").setLevel(logging.DEBUG)
logging.getLogger("context_analyzer").setLevel(logging.DEBUG)
```