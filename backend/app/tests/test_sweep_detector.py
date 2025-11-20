"""Tests for SweepDetector service."""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from app.services.sweep_detector import SweepDetector, init_sweep_detector, get_sweep_detector
from app.models.indicators import Signal


@pytest.fixture
def sweep_detector():
    """Create a fresh SweepDetector instance."""
    return SweepDetector()


def test_sweep_detector_initialization(sweep_detector):
    """Test SweepDetector can be initialized."""
    assert sweep_detector is not None
    assert len(sweep_detector.signals) == 0
    assert len(sweep_detector.cvd_history) == 0
    assert len(sweep_detector.vol_delta_history) == 0


def test_cvd_divergence_detection(sweep_detector):
    """Test CVD divergence detection."""
    # Not enough history
    result = sweep_detector._detect_cvd_divergence(100.0)
    assert result is False
    
    # Build history with bullish divergence
    for i in range(20):
        price = 100 - (i * 0.1)  # Price decreasing
        cvd = i * 100  # CVD increasing
        sweep_detector.cvd_history.append({
            "time": datetime.now(timezone.utc),
            "cvd": cvd,
            "price": price,
        })
    
    # Should detect bullish divergence
    result = sweep_detector._detect_cvd_divergence(98.0)
    assert result is True


def test_volume_delta_spike_detection(sweep_detector):
    """Test volume delta spike detection."""
    # Not enough history
    result = sweep_detector._detect_volume_delta_spike(100.0)
    assert result is False
    
    # Build history with baseline
    for i in range(20):
        delta = 10.0 + (i * 0.1)
        sweep_detector.vol_delta_history.append({
            "time": datetime.now(timezone.utc),
            "volume_delta": delta,
        })
    
    # Small delta should not trigger spike
    result = sweep_detector._detect_volume_delta_spike(15.0)
    assert result is False
    
    # Large delta should trigger spike (> 1.5x average)
    result = sweep_detector._detect_volume_delta_spike(50.0)
    assert result is True


@pytest.mark.asyncio
async def test_signal_generation(sweep_detector):
    """Test signal generation with confluence."""
    # Build sufficient history
    for i in range(20):
        sweep_detector.cvd_history.append({
            "time": datetime.now(timezone.utc),
            "cvd": i * 100,
            "price": 100 - (i * 0.1),
        })
        sweep_detector.vol_delta_history.append({
            "time": datetime.now(timezone.utc),
            "volume_delta": 10 + (i * 1),
        })
    
    # Analyze with conditions met
    signal = await sweep_detector.analyze(
        current_price=98.0,
        cvd_snapshot={"cvd": 2000},
        vol_delta_snapshot={"volume_delta": 50},
        liquidation_support=97.0,
        liquidation_resistance=99.0,
    )
    
    assert signal is not None
    assert signal.setup_type == "bullish_sweep"
    assert signal.entry_price == 98.0
    assert signal.stop_loss < signal.entry_price
    assert signal.take_profit > signal.entry_price
    assert signal.risk_reward > 0
    assert signal.confluence_score > 50
    assert signal.cvd_divergence is True
    assert signal.liquidation_support == 97.0
    assert signal.liquidation_resistance == 99.0


@pytest.mark.asyncio
async def test_no_signal_without_divergence(sweep_detector):
    """Test that signal is not generated without CVD divergence."""
    # Build history WITHOUT divergence
    for i in range(20):
        sweep_detector.cvd_history.append({
            "time": datetime.now(timezone.utc),
            "cvd": 100 - (i * 5),  # CVD decreasing
            "price": 100 - (i * 0.1),  # Price also decreasing
        })
        sweep_detector.vol_delta_history.append({
            "time": datetime.now(timezone.utc),
            "volume_delta": 50,  # Constant, no spike
        })
    
    signal = await sweep_detector.analyze(
        current_price=98.0,
        cvd_snapshot={"cvd": 100},
        vol_delta_snapshot={"volume_delta": 50},
    )
    
    assert signal is None


@pytest.mark.asyncio
async def test_no_signal_without_spike(sweep_detector):
    """Test that signal is not generated without volume delta spike."""
    # Build history with divergence but no spike
    for i in range(20):
        sweep_detector.cvd_history.append({
            "time": datetime.now(timezone.utc),
            "cvd": i * 100,  # CVD increasing
            "price": 100 - (i * 0.1),  # Price decreasing
        })
        sweep_detector.vol_delta_history.append({
            "time": datetime.now(timezone.utc),
            "volume_delta": 5.0,  # Small, consistent value
        })
    
    signal = await sweep_detector.analyze(
        current_price=98.0,
        cvd_snapshot={"cvd": 2000},
        vol_delta_snapshot={"volume_delta": 5.0},  # No spike
    )
    
    assert signal is None


def test_get_last_signal(sweep_detector):
    """Test getting the last signal."""
    assert sweep_detector.get_last_signal() is None
    
    # Add a signal manually
    signal = Signal(
        timestamp=datetime.now(timezone.utc),
        setup_type="bullish_sweep",
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
        confluence_score=85.0,
        cvd_value=1000.0,
        cvd_divergence=True,
        volume_delta=50.0,
        volume_delta_percentile=75.0,
        reason="Test signal"
    )
    sweep_detector.signals.append(signal)
    
    last = sweep_detector.get_last_signal()
    assert last is not None
    assert last.setup_type == "bullish_sweep"
    assert last.entry_price == 100.0


def test_get_signals_history(sweep_detector):
    """Test getting signal history."""
    assert sweep_detector.get_signals_history() == []
    
    # Add multiple signals
    for i in range(5):
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            setup_type="bullish_sweep",
            entry_price=100.0 + i,
            stop_loss=99.0 + i,
            take_profit=103.0 + i,
            risk_reward=3.0,
            confluence_score=85.0,
            cvd_value=1000.0,
            cvd_divergence=True,
            volume_delta=50.0,
            volume_delta_percentile=75.0,
            reason=f"Signal {i}"
        )
        sweep_detector.signals.append(signal)
    
    history = sweep_detector.get_signals_history(limit=10)
    assert len(history) == 5
    
    history_limited = sweep_detector.get_signals_history(limit=2)
    assert len(history_limited) == 2


def test_signal_model_serialization():
    """Test that Signal model can be properly serialized."""
    signal = Signal(
        timestamp=datetime.now(timezone.utc),
        setup_type="bullish_sweep",
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
        confluence_score=85.0,
        cvd_value=1000.0,
        cvd_divergence=True,
        volume_delta=50.0,
        volume_delta_percentile=75.0,
        liquidation_support=98.0,
        liquidation_resistance=102.0,
        reason="Test signal"
    )
    
    # Should be able to serialize to JSON
    json_str = signal.json()
    assert "bullish_sweep" in json_str
    assert "100" in json_str
    
    # Should be able to convert to dict
    signal_dict = signal.dict()
    assert signal_dict["entry_price"] == 100.0
    assert signal_dict["setup_type"] == "bullish_sweep"


def test_volume_delta_percentile_calculation(sweep_detector):
    """Test volume delta percentile calculation."""
    # Without history
    percentile = sweep_detector._calculate_volume_delta_percentile()
    assert percentile == 50.0
    
    # Build history
    for i in range(1, 11):
        sweep_detector.vol_delta_history.append({
            "time": datetime.now(timezone.utc),
            "volume_delta": float(i * 10),
        })
    
    # Current delta at 50
    sweep_detector.vol_delta_history.append({
        "time": datetime.now(timezone.utc),
        "volume_delta": 50.0,
    })
    
    percentile = sweep_detector._calculate_volume_delta_percentile()
    assert 0 <= percentile <= 100


def test_init_and_get_sweep_detector():
    """Test initialization and retrieval of global sweep detector."""
    detector = init_sweep_detector()
    assert detector is not None
    
    retrieved = get_sweep_detector()
    assert retrieved is detector
