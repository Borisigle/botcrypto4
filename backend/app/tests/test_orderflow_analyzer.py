"""Tests for OrderFlowAnalyzer."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.strategy.analyzers.orderflow import OrderFlowAnalyzer, get_orderflow_analyzer
from app.strategy.metrics import MetricsCalculator
from app.ws.models import Settings, TradeSide, TradeTick


def _make_trade(
    ts: datetime,
    price: float,
    qty: float,
    side: TradeSide,
    trade_id: int,
) -> TradeTick:
    """Create a TradeTick for testing."""
    return TradeTick(
        ts=ts,
        price=price,
        qty=qty,
        side=side,
        isBuyerMaker=side == TradeSide.SELL,
        id=trade_id,
    )


class TestOrderFlowAnalyzer:
    """Test OrderFlowAnalyzer functionality."""

    def test_initialization(self):
        """Test OrderFlowAnalyzer initialization."""
        analyzer = OrderFlowAnalyzer()
        assert analyzer.calculation_interval == 50
        assert analyzer._sum_price_qty == 0.0
        assert analyzer._sum_qty == 0.0
        assert analyzer._trade_count == 0
        assert analyzer._latest_metrics is None

    def test_ingest_single_trade(self):
        """Test ingesting a single trade."""
        analyzer = OrderFlowAnalyzer(calculation_interval=100)
        trade = _make_trade(
            datetime.now(timezone.utc),
            100.0,
            1.0,
            TradeSide.BUY,
            1,
        )
        analyzer.ingest_trade(trade)

        assert analyzer._trade_count == 1
        assert analyzer._sum_price_qty == 100.0
        assert analyzer._sum_qty == 1.0
        assert analyzer._latest_metrics is None  # Not calculated yet

    def test_metrics_calculation_interval(self):
        """Test metrics are calculated at correct interval."""
        analyzer = OrderFlowAnalyzer(calculation_interval=5)
        trades = []

        for i in range(10):
            trade = _make_trade(
                datetime.now(timezone.utc),
                100.0 + (i * 0.1),
                1.0,
                TradeSide.BUY if i % 2 == 0 else TradeSide.SELL,
                i,
            )
            trades.append(trade)

        # Ingest first 4 trades - no calculation
        for i in range(4):
            analyzer.ingest_trade(trades[i])
            assert analyzer._latest_metrics is None

        # 5th trade should trigger calculation
        analyzer.ingest_trade(trades[4])
        assert analyzer._latest_metrics is not None
        assert analyzer._latest_metrics["trade_count"] == 5

        # Ingest more trades without triggering
        for i in range(5, 9):
            analyzer.ingest_trade(trades[i])
            # Metrics should still reflect first batch
            assert analyzer._latest_metrics["trade_count"] == 5

        # 10th trade should trigger second calculation
        analyzer.ingest_trade(trades[9])
        assert analyzer._latest_metrics["trade_count"] == 10

    def test_get_latest_metrics(self):
        """Test retrieving latest metrics."""
        analyzer = OrderFlowAnalyzer(calculation_interval=2)
        
        # No metrics initially
        assert analyzer.get_latest_metrics() is None

        # Add trades
        trade1 = _make_trade(datetime.now(timezone.utc), 100.0, 1.0, TradeSide.BUY, 1)
        trade2 = _make_trade(datetime.now(timezone.utc), 101.0, 2.0, TradeSide.SELL, 2)

        analyzer.ingest_trade(trade1)
        analyzer.ingest_trade(trade2)

        metrics = analyzer.get_latest_metrics()
        assert metrics is not None
        assert metrics["trade_count"] == 2
        assert metrics["delta"] == -1.0  # 1 buy - 2 sells

    def test_get_metrics_with_metadata(self):
        """Test metrics endpoint response format."""
        analyzer = OrderFlowAnalyzer(calculation_interval=2)

        trade1 = _make_trade(datetime.now(timezone.utc), 100.0, 1.0, TradeSide.BUY, 1)
        trade2 = _make_trade(datetime.now(timezone.utc), 101.0, 2.0, TradeSide.SELL, 2)

        analyzer.ingest_trade(trade1)
        analyzer.ingest_trade(trade2)

        result = analyzer.get_metrics_with_metadata()

        assert "metrics" in result
        assert "metadata" in result
        assert result["metrics"]["trade_count"] == 2
        assert result["metadata"]["last_update"] is not None
        assert result["metadata"]["trade_count"] == 2
        assert result["metadata"]["cumulative_volume"] == 3.0  # 1.0 + 2.0

    def test_reset_state(self):
        """Test state reset functionality."""
        analyzer = OrderFlowAnalyzer(calculation_interval=2)

        trade1 = _make_trade(datetime.now(timezone.utc), 100.0, 1.0, TradeSide.BUY, 1)
        trade2 = _make_trade(datetime.now(timezone.utc), 101.0, 2.0, TradeSide.SELL, 2)

        analyzer.ingest_trade(trade1)
        analyzer.ingest_trade(trade2)

        assert analyzer._trade_count == 2
        assert analyzer._sum_qty == 3.0

        # Reset state
        analyzer.reset_state()

        assert analyzer._trade_count == 0
        assert analyzer._sum_qty == 0.0
        assert analyzer._sum_price_qty == 0.0
        assert analyzer._latest_metrics is None

    def test_custom_metrics_calculator(self):
        """Test with custom MetricsCalculator."""
        custom_calc = MetricsCalculator(tick_size=0.01)
        analyzer = OrderFlowAnalyzer(
            metrics_calculator=custom_calc,
            calculation_interval=1,
        )

        trade = _make_trade(datetime.now(timezone.utc), 100.123, 1.0, TradeSide.BUY, 1)
        analyzer.ingest_trade(trade)

        metrics = analyzer.get_latest_metrics()
        assert metrics is not None
        # With smaller tick size, POC should be precise
        assert abs(metrics["poc"] - 100.12) < 0.01

    def test_global_singleton(self):
        """Test global singleton pattern."""
        analyzer1 = get_orderflow_analyzer()
        analyzer2 = get_orderflow_analyzer()

        assert analyzer1 is analyzer2

    def test_mixed_buy_sell_trades(self):
        """Test with realistic buy/sell mix."""
        analyzer = OrderFlowAnalyzer(calculation_interval=4)
        trades = [
            _make_trade(datetime.now(timezone.utc), 100.0, 5.0, TradeSide.BUY, 1),
            _make_trade(datetime.now(timezone.utc), 100.1, 3.0, TradeSide.SELL, 2),
            _make_trade(datetime.now(timezone.utc), 100.2, 4.0, TradeSide.BUY, 3),
            _make_trade(datetime.now(timezone.utc), 100.3, 2.0, TradeSide.SELL, 4),
        ]

        for trade in trades:
            analyzer.ingest_trade(trade)

        metrics = analyzer.get_latest_metrics()
        assert metrics is not None

        # 2 buys (5+4=9) vs 2 sells (3+2=5) -> delta = 4
        assert metrics["delta"] == 4.0
        assert metrics["buy_volume"] == 9.0
        assert metrics["sell_volume"] == 5.0
        assert metrics["trade_count"] == 4

    def test_large_volume_trades(self):
        """Test with large volume trades."""
        analyzer = OrderFlowAnalyzer(calculation_interval=2)
        trades = [
            _make_trade(datetime.now(timezone.utc), 100.0, 1000.0, TradeSide.BUY, 1),
            _make_trade(datetime.now(timezone.utc), 100.5, 500.0, TradeSide.SELL, 2),
        ]

        for trade in trades:
            analyzer.ingest_trade(trade)

        metrics = analyzer.get_latest_metrics()
        assert metrics is not None
        assert metrics["delta"] == 500.0
        assert metrics["buy_volume"] == 1000.0
        assert metrics["sell_volume"] == 500.0

    def test_rapid_succession_trades(self):
        """Test handling rapid succession of trades."""
        analyzer = OrderFlowAnalyzer(calculation_interval=10)
        
        # Add 20 trades rapidly
        for i in range(20):
            trade = _make_trade(
                datetime.now(timezone.utc),
                100.0 + (i % 5) * 0.1,
                1.0,
                TradeSide.BUY if i % 2 == 0 else TradeSide.SELL,
                i,
            )
            analyzer.ingest_trade(trade)

        # Should have calculated twice (at 10 and 20)
        metrics = analyzer.get_latest_metrics()
        assert metrics is not None
        assert metrics["trade_count"] == 20
        assert analyzer._sum_qty == 20.0  # 20 trades x 1.0 qty each

    def test_metadata_timestamp_format(self):
        """Test metadata returns proper ISO format timestamp."""
        analyzer = OrderFlowAnalyzer(calculation_interval=1)
        trade = _make_trade(datetime.now(timezone.utc), 100.0, 1.0, TradeSide.BUY, 1)
        analyzer.ingest_trade(trade)

        result = analyzer.get_metrics_with_metadata()
        last_update = result["metadata"]["last_update"]

        # Should be ISO format string
        assert isinstance(last_update, str)
        assert "T" in last_update
        assert "Z" in last_update or "+" in last_update

    def test_initialize_from_backfill(self):
        """Test initializing analyzer from backfill trades."""
        analyzer = OrderFlowAnalyzer(calculation_interval=50)
        
        # Create backfill trades
        backfill_trades = [
            _make_trade(
                datetime.now(timezone.utc),
                50000.0 + i * 10,
                1.0,
                TradeSide.BUY if i % 2 == 0 else TradeSide.SELL,
                i
            )
            for i in range(100)
        ]
        
        # Initialize from backfill
        analyzer.initialize_from_backfill(backfill_trades)
        
        # Check state
        assert analyzer._trade_count == 100
        assert analyzer._sum_qty == 100.0
        assert analyzer._sum_price_qty > 0
        
        # Metrics should be calculated
        metrics = analyzer.get_latest_metrics()
        assert metrics is not None
        assert metrics["trade_count"] == 100
        assert metrics["vwap"] is not None
        assert metrics["poc"] is not None

    def test_initialize_from_state(self):
        """Test initializing analyzer from pre-calculated state."""
        analyzer = OrderFlowAnalyzer(calculation_interval=50)
        
        # Initialize with state
        analyzer.initialize_from_state(
            sum_price_qty=5000000.0,
            sum_qty=100.0,
            volume_by_price={50000.0: 50.0, 50010.0: 50.0},
            buy_volume=50.0,
            sell_volume=50.0,
            trade_count=100,
        )
        
        # Check state
        assert analyzer._trade_count == 100
        assert analyzer._sum_qty == 100.0
        assert analyzer._sum_price_qty == 5000000.0
        assert analyzer._buy_volume == 50.0
        assert analyzer._sell_volume == 50.0
        
        # Metrics should be calculated
        metrics = analyzer.get_latest_metrics()
        assert metrics is not None
        assert metrics["vwap"] == 50000.0
        assert metrics["trade_count"] == 100

    def test_incremental_vwap_calculation(self):
        """Test that VWAP updates correctly with new trades."""
        analyzer = OrderFlowAnalyzer(calculation_interval=2)
        
        # First trade: price=100, qty=1 -> VWAP=100
        trade1 = _make_trade(datetime.now(timezone.utc), 100.0, 1.0, TradeSide.BUY, 1)
        analyzer.ingest_trade(trade1)
        
        # Second trade: price=200, qty=1 -> VWAP=150
        trade2 = _make_trade(datetime.now(timezone.utc), 200.0, 1.0, TradeSide.BUY, 2)
        analyzer.ingest_trade(trade2)
        
        metrics = analyzer.get_latest_metrics()
        assert metrics is not None
        assert metrics["vwap"] == 150.0  # (100*1 + 200*1) / (1+1)
        
        # Third trade: price=300, qty=2 -> VWAP=225
        trade3 = _make_trade(datetime.now(timezone.utc), 300.0, 2.0, TradeSide.BUY, 3)
        analyzer.ingest_trade(trade3)
        # Force calculation
        analyzer._update_metrics()
        
        metrics = analyzer.get_latest_metrics()
        assert metrics is not None
        assert metrics["vwap"] == 225.0  # (100*1 + 200*1 + 300*2) / (1+1+2) = 900/4
