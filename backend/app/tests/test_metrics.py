"""Tests for metrics system."""
import pytest
from app.ws.metrics import MetricsRecorder


def test_metrics_recorder_init() -> None:
    """Test MetricsRecorder initialization."""
    recorder = MetricsRecorder(60)
    
    assert recorder.window_sec == 60
    # Should start with zero metrics
    snapshot = recorder.snapshot(trade_queue_size=0, depth_queue_size=0)
    assert snapshot.trades.per_minute_count == 0
    assert snapshot.trades.per_second_rate == 0.0
    assert snapshot.trades.queue_size == 0


def test_metrics_recorder_record_trade() -> None:
    """Test recording trade metrics."""
    recorder = MetricsRecorder(60)
    
    # Record some trades
    for _ in range(30):
        recorder.record_trade()
    
    snapshot = recorder.snapshot(trade_queue_size=10, depth_queue_size=20)
    
    assert snapshot.trades.per_minute_count >= 30
    assert snapshot.trades.queue_size == 10
    assert snapshot.depth.queue_size == 20


def test_metrics_recorder_record_depth() -> None:
    """Test recording depth metrics."""
    recorder = MetricsRecorder(60)
    
    # Record some depth updates
    for _ in range(100):
        recorder.record_depth()
    
    snapshot = recorder.snapshot(trade_queue_size=0, depth_queue_size=15)
    
    assert snapshot.depth.per_minute_count >= 100
    assert snapshot.trades.queue_size == 0
    assert snapshot.depth.queue_size == 15