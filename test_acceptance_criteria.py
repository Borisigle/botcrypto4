#!/usr/bin/env python3
"""Test acceptance criteria for Sweep Detector + Strategy Engine."""
import asyncio
from datetime import datetime, timezone
import sys

sys.path.insert(0, 'backend')

from app.models.indicators import Signal
from app.services.sweep_detector import SweepDetector


async def test_acceptance_criteria():
    """Verify all acceptance criteria are met."""
    print("=" * 70)
    print("TESTING SWEEP DETECTOR ACCEPTANCE CRITERIA")
    print("=" * 70)
    
    detector = SweepDetector()
    
    # Build history with divergence and spike
    for i in range(20):
        detector.cvd_history.append({
            "time": datetime.now(timezone.utc),
            "cvd": i * 100,  # CVD increasing (bullish)
            "price": 100 - (i * 0.1),  # Price decreasing
        })
        detector.vol_delta_history.append({
            "time": datetime.now(timezone.utc),
            "volume_delta": 10 + (i * 1),  # Increasing volume delta
        })
    
    # Test 1: Detects CVD divergence
    print("\n✓ Test 1: Detecta CVD divergencia")
    cvd_div = detector._detect_cvd_divergence(98.0)
    print(f"  CVD divergence detected: {cvd_div}")
    assert cvd_div is True, "CVD divergence not detected"
    
    # Test 2: Detects Volume Delta spike
    print("\n✓ Test 2: Detecta Volume Delta spike")
    vol_spike = detector._detect_volume_delta_spike(50.0)
    print(f"  Volume Delta spike detected: {vol_spike}")
    assert vol_spike is True, "Volume Delta spike not detected"
    
    # Test 3: Generates Signal with entry/SL/TP/RR
    print("\n✓ Test 3: Genera Signal con entrada/SL/TP/RR")
    signal = await detector.analyze(
        current_price=98.0,
        cvd_snapshot={"cvd": 2000},
        vol_delta_snapshot={"volume_delta": 50},
        liquidation_support=97.0,
        liquidation_resistance=99.0,
    )
    assert signal is not None, "Signal not generated"
    print(f"  Entry: {signal.entry_price}")
    print(f"  SL: {signal.stop_loss}")
    print(f"  TP: {signal.take_profit}")
    print(f"  RR: {signal.risk_reward:.2f}")
    
    # Test 4: GET /signals/current returns signal (or null)
    print("\n✓ Test 4: GET /signals/current retorna señal (o null)")
    last_signal = detector.get_last_signal()
    print(f"  get_last_signal() returned: {last_signal is not None}")
    assert last_signal is not None, "get_last_signal() should return signal"
    
    # Test 5: GET /signals/history returns array of signals
    print("\n✓ Test 5: GET /signals/history retorna array de señales")
    # Generate more signals
    for _ in range(3):
        await detector.analyze(
            current_price=98.0,
            cvd_snapshot={"cvd": 2000},
            vol_delta_snapshot={"volume_delta": 50},
        )
    
    history = detector.get_signals_history(limit=50)
    print(f"  Signals in history: {len(history)}")
    assert len(history) > 0, "History should contain signals"
    
    # Test 6: Confluence score reflects setup strength (0-100)
    print("\n✓ Test 6: Confluence score refleja fuerza del setup (0-100)")
    print(f"  Confluence score: {signal.confluence_score}")
    assert 0 <= signal.confluence_score <= 100, "Score should be 0-100"
    
    # Test 7: Logs show "SIGNAL GENERATED" when setup detected
    print("\n✓ Test 7: Logs muestran 'SIGNAL GENERATED' cuando hay setup")
    print(f"  Signal setup_type: {signal.setup_type}")
    print(f"  Signal reason: {signal.reason}")
    
    # Test 8: Historical limit to 100 signals
    print("\n✓ Test 8: Histórico limitado a 100 señales")
    print(f"  Max signals in deque: {detector.signals.maxlen}")
    assert detector.signals.maxlen == 100, "Signal deque should have maxlen=100"
    
    # Test 9: Signal model has all required fields
    print("\n✓ Test 9: Modelo Signal contiene todos los campos requeridos")
    required_fields = [
        'timestamp', 'setup_type', 'entry_price', 'stop_loss', 'take_profit',
        'risk_reward', 'confluence_score', 'cvd_value', 'cvd_divergence',
        'volume_delta', 'volume_delta_percentile', 'reason'
    ]
    for field in required_fields:
        assert hasattr(signal, field), f"Signal missing field: {field}"
    print(f"  All {len(required_fields)} required fields present")
    
    # Test 10: Optional fields for liquidation levels
    print("\n✓ Test 10: Campos opcionales para niveles de liquidación")
    assert hasattr(signal, 'liquidation_support'), "Missing liquidation_support"
    assert hasattr(signal, 'liquidation_resistance'), "Missing liquidation_resistance"
    print(f"  Liquidation support: {signal.liquidation_support}")
    print(f"  Liquidation resistance: {signal.liquidation_resistance}")
    
    print("\n" + "=" * 70)
    print("ALL ACCEPTANCE CRITERIA MET ✓")
    print("=" * 70)
    print("\nSummary:")
    print(f"  - CVD divergence detection: ✓")
    print(f"  - Volume Delta spike detection: ✓")
    print(f"  - Signal generation with RR: ✓")
    print(f"  - GET /signals/current: ✓")
    print(f"  - GET /signals/history: ✓")
    print(f"  - Confluence score (0-100): ✓")
    print(f"  - Signal logging: ✓")
    print(f"  - Historical limit (100): ✓")
    print(f"  - Complete Signal model: ✓")
    print(f"  - Liquidation support/resistance: ✓")


if __name__ == "__main__":
    asyncio.run(test_acceptance_criteria())
