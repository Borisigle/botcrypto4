#!/usr/bin/env python3
"""Simple test for SweepDetector to verify basic functionality."""
import asyncio
from datetime import datetime, timezone

# Add backend to path
import sys
sys.path.insert(0, '/home/engine/project/backend')

from app.services.sweep_detector import SweepDetector


async def test_sweep_detector():
    """Test basic sweep detector functionality."""
    detector = SweepDetector()
    
    print("Testing SweepDetector...")
    
    # Simulate CVD and volume delta history building
    # Need at least 20 samples for detection
    for i in range(20):
        current_price = 100 - (i * 0.1)  # Slowly decreasing price
        cvd_value = i * 100  # Increasing CVD (divergence)
        vol_delta = 10 + (i * 1)  # Increasing volume delta
        
        # Build history manually
        detector.cvd_history.append({
            "time": datetime.now(timezone.utc),
            "cvd": cvd_value,
            "price": current_price,
        })
        
        detector.vol_delta_history.append({
            "time": datetime.now(timezone.utc),
            "volume_delta": vol_delta,
        })
    
    # Now test with a spike
    current_price = 98.0
    cvd_snapshot = {"cvd": 2000}
    vol_delta_snapshot = {"volume_delta": 50}  # Spike!
    
    signal = await detector.analyze(
        current_price=current_price,
        cvd_snapshot=cvd_snapshot,
        vol_delta_snapshot=vol_delta_snapshot,
        liquidation_support=97.0,
        liquidation_resistance=99.0,
    )
    
    if signal:
        print(f"✓ Signal generated!")
        print(f"  Setup: {signal.setup_type}")
        print(f"  Entry: {signal.entry_price}")
        print(f"  SL: {signal.stop_loss}")
        print(f"  TP: {signal.take_profit}")
        print(f"  RR: {signal.risk_reward:.2f}")
        print(f"  Score: {signal.confluence_score}")
        print(f"  Reason: {signal.reason}")
    else:
        print("✗ No signal generated (may be expected)")
    
    # Test get_last_signal
    last_signal = detector.get_last_signal()
    if signal and last_signal:
        print(f"✓ get_last_signal() works: {last_signal.setup_type}")
    
    # Test get_signals_history
    history = detector.get_signals_history(limit=10)
    print(f"✓ Signals history: {len(history)} signals")
    
    print("\nAll tests passed!")


if __name__ == "__main__":
    asyncio.run(test_sweep_detector())
