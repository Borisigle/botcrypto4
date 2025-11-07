"""Tests for MetricsCalculator."""
from __future__ import annotations

import pytest

from app.strategy.metrics import MetricsCalculator


def _make_trade(
    price: float,
    qty: float,
    is_buyer_maker: bool,
    timestamp: float = 1234567890.0,
) -> dict:
    """Create a trade dictionary for testing."""
    return {
        "price": price,
        "qty": qty,
        "is_buyer_maker": is_buyer_maker,
        "timestamp": timestamp,
    }


class TestMetricsCalculatorBasics:
    """Test basic MetricsCalculator functionality."""

    def test_initialization(self):
        """Test MetricsCalculator initialization."""
        calc = MetricsCalculator(tick_size=0.1)
        assert calc.tick_size == 0.1

    def test_empty_trades_list(self):
        """Test with empty trades list."""
        calc = MetricsCalculator()
        result = calc.calculate([])

        assert result["vwap"] is None
        assert result["poc"] is None
        assert result["delta"] == 0.0
        assert result["buy_volume"] == 0.0
        assert result["sell_volume"] == 0.0
        assert result["footprint"] == []
        assert result["trade_count"] == 0

    def test_single_trade(self):
        """Test with a single trade."""
        calc = MetricsCalculator(tick_size=0.01)
        trades = [_make_trade(price=100.5, qty=1.0, is_buyer_maker=False)]
        result = calc.calculate(trades)

        assert result["vwap"] is not None
        assert result["poc"] == 100.5
        assert result["delta"] == 1.0
        assert result["buy_volume"] == 1.0
        assert result["sell_volume"] == 0.0
        assert result["trade_count"] == 1
        assert len(result["footprint"]) == 1
        assert result["footprint"][0]["price"] == 100.5
        assert result["footprint"][0]["volume"] == 1.0


class TestVWAP:
    """Test VWAP calculation."""

    def test_vwap_simple(self):
        """Test VWAP with simple trades."""
        calc = MetricsCalculator()
        # Two trades at different prices with different volumes
        trades = [
            _make_trade(price=100.0, qty=1.0, is_buyer_maker=False),
            _make_trade(price=102.0, qty=2.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        # VWAP should be (100*1 + 102*2) / (1+2) = 304/3 = 101.333...
        assert result["vwap"] is not None
        assert abs(result["vwap"] - 101.333) < 0.01

    def test_vwap_many_trades(self):
        """Test VWAP with many trades."""
        calc = MetricsCalculator()
        trades = []
        # Create 100 trades with varying prices
        for i in range(100):
            price = 100.0 + (i % 10) * 0.1
            qty = 1.0 + (i % 5)
            trades.append(_make_trade(
                price=price,
                qty=qty,
                is_buyer_maker=i % 2 == 0,
            ))

        result = calc.calculate(trades)
        assert result["vwap"] is not None
        assert 100.0 <= result["vwap"] <= 100.9

    def test_vwap_precision(self):
        """Test VWAP precision matches manual calculation within tolerance."""
        calc = MetricsCalculator()
        trades = [
            _make_trade(price=100.0, qty=10.0, is_buyer_maker=False),
            _make_trade(price=101.0, qty=5.0, is_buyer_maker=False),
            _make_trade(price=102.0, qty=5.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        # Manual calculation: (100*10 + 101*5 + 102*5) / (10+5+5) = 2055/20 = 102.75
        expected_vwap = (100.0 * 10 + 101.0 * 5 + 102.0 * 5) / 20.0
        assert result["vwap"] is not None
        assert abs(result["vwap"] - expected_vwap) < 0.01


class TestPOC:
    """Test POC (Point of Control) calculation."""

    def test_poc_single_price_level(self):
        """Test POC with trades at single price level."""
        calc = MetricsCalculator(tick_size=0.1)
        trades = [
            _make_trade(price=100.0, qty=1.0, is_buyer_maker=False),
            _make_trade(price=100.0, qty=2.0, is_buyer_maker=True),
            _make_trade(price=100.0, qty=1.5, is_buyer_maker=False),
        ]
        result = calc.calculate(trades)

        assert result["poc"] == 100.0

    def test_poc_multiple_price_levels(self):
        """Test POC with trades at multiple price levels."""
        calc = MetricsCalculator(tick_size=0.1)
        trades = [
            _make_trade(price=100.0, qty=5.0, is_buyer_maker=False),  # bin 100.0: 5.0
            _make_trade(price=100.05, qty=3.0, is_buyer_maker=True),  # bin 100.0: 8.0
            _make_trade(price=101.0, qty=10.0, is_buyer_maker=False),  # bin 101.0: 10.0 <- highest
            _make_trade(price=102.0, qty=2.0, is_buyer_maker=False),  # bin 102.0: 2.0
        ]
        result = calc.calculate(trades)

        assert result["poc"] == 101.0

    def test_poc_binning_precision(self):
        """Test POC respects tick_size binning."""
        calc = MetricsCalculator(tick_size=1.0)
        trades = [
            _make_trade(price=100.2, qty=1.0, is_buyer_maker=False),
            _make_trade(price=100.8, qty=2.0, is_buyer_maker=False),
            _make_trade(price=101.1, qty=1.0, is_buyer_maker=False),
        ]
        result = calc.calculate(trades)

        # 100.2 and 100.8 should bin to 100, with total volume 3.0
        # 101.1 should bin to 101, with volume 1.0
        # POC should be 100.0
        assert result["poc"] == 100.0


class TestDelta:
    """Test cumulative delta calculation."""

    def test_delta_equal_volumes(self):
        """Test delta when buy and sell volumes are equal."""
        calc = MetricsCalculator()
        trades = [
            _make_trade(price=100.0, qty=5.0, is_buyer_maker=False),
            _make_trade(price=101.0, qty=5.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        assert result["delta"] == 0.0

    def test_delta_more_buy_volume(self):
        """Test delta when buy volume > sell volume."""
        calc = MetricsCalculator()
        trades = [
            _make_trade(price=100.0, qty=10.0, is_buyer_maker=False),
            _make_trade(price=101.0, qty=3.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        assert result["delta"] == 7.0

    def test_delta_more_sell_volume(self):
        """Test delta when sell volume > buy volume."""
        calc = MetricsCalculator()
        trades = [
            _make_trade(price=100.0, qty=3.0, is_buyer_maker=False),
            _make_trade(price=101.0, qty=10.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        assert result["delta"] == -7.0

    def test_delta_all_buys(self):
        """Test delta when all trades are buys."""
        calc = MetricsCalculator()
        trades = [
            _make_trade(price=100.0, qty=5.0, is_buyer_maker=False),
            _make_trade(price=100.5, qty=3.0, is_buyer_maker=False),
        ]
        result = calc.calculate(trades)

        assert result["delta"] == 8.0
        assert result["buy_volume"] == 8.0
        assert result["sell_volume"] == 0.0

    def test_delta_all_sells(self):
        """Test delta when all trades are sells."""
        calc = MetricsCalculator()
        trades = [
            _make_trade(price=100.0, qty=5.0, is_buyer_maker=True),
            _make_trade(price=100.5, qty=3.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        assert result["delta"] == -8.0
        assert result["buy_volume"] == 0.0
        assert result["sell_volume"] == 8.0


class TestFootprint:
    """Test footprint calculation."""

    def test_footprint_empty(self):
        """Test footprint with no trades."""
        calc = MetricsCalculator()
        result = calc.calculate([])

        assert result["footprint"] == []

    def test_footprint_single_level(self):
        """Test footprint with single price level."""
        calc = MetricsCalculator(tick_size=0.1)
        trades = [
            _make_trade(price=100.0, qty=1.0, is_buyer_maker=False),
            _make_trade(price=100.05, qty=2.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        assert len(result["footprint"]) == 1
        assert result["footprint"][0]["price"] == 100.0
        assert result["footprint"][0]["volume"] == 3.0
        assert result["footprint"][0]["buy_vol"] == 1.0
        assert result["footprint"][0]["sell_vol"] == 2.0
        assert result["footprint"][0]["rank"] == 1

    def test_footprint_multiple_levels(self):
        """Test footprint with multiple price levels."""
        calc = MetricsCalculator(tick_size=1.0)
        trades = [
            _make_trade(price=100.0, qty=5.0, is_buyer_maker=False),
            _make_trade(price=101.0, qty=10.0, is_buyer_maker=True),
            _make_trade(price=102.0, qty=3.0, is_buyer_maker=False),
            _make_trade(price=103.0, qty=2.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        assert len(result["footprint"]) == 4
        # Should be sorted by volume descending: 101(10), 100(5), 102(3), 103(2)
        assert result["footprint"][0]["price"] == 101.0
        assert result["footprint"][0]["volume"] == 10.0
        assert result["footprint"][0]["rank"] == 1

        assert result["footprint"][1]["price"] == 100.0
        assert result["footprint"][1]["volume"] == 5.0
        assert result["footprint"][1]["rank"] == 2

    def test_footprint_top_20_limit(self):
        """Test footprint returns max 20 levels."""
        calc = MetricsCalculator(tick_size=1.0)
        trades = []
        # Create 30 different price levels
        for i in range(30):
            trades.append(_make_trade(
                price=100.0 + i,
                qty=30.0 - i,  # decreasing volume
                is_buyer_maker=i % 2 == 0,
            ))

        result = calc.calculate(trades)

        assert len(result["footprint"]) == 20
        # Should be sorted by volume descending
        for i in range(len(result["footprint"]) - 1):
            assert result["footprint"][i]["volume"] >= result["footprint"][i + 1]["volume"]
            assert result["footprint"][i]["rank"] < result["footprint"][i + 1]["rank"]

    def test_footprint_buy_sell_breakdown(self):
        """Test footprint correctly breaks down buy and sell volumes."""
        calc = MetricsCalculator(tick_size=0.1)
        trades = [
            _make_trade(price=100.0, qty=5.0, is_buyer_maker=False),
            _make_trade(price=100.0, qty=3.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        assert result["footprint"][0]["volume"] == 8.0
        assert result["footprint"][0]["buy_vol"] == 5.0
        assert result["footprint"][0]["sell_vol"] == 3.0


class TestMetricsWithRealWorldScenario:
    """Test with realistic market data scenarios."""

    def test_100_trades_synthetic(self):
        """Test with 100 synthetic trades."""
        calc = MetricsCalculator(tick_size=0.01)
        trades = []

        base_price = 100.0
        for i in range(100):
            # Create realistic price movement
            price_offset = (i % 20) * 0.05 - 0.5
            price = base_price + price_offset
            qty = 0.1 + (i % 10) * 0.05
            is_buyer_maker = i % 2 == 0

            trades.append(_make_trade(
                price=price,
                qty=qty,
                is_buyer_maker=is_buyer_maker,
                timestamp=1234567890.0 + i,
            ))

        result = calc.calculate(trades)

        # Verify all fields are populated
        assert result["vwap"] is not None
        assert result["poc"] is not None
        assert result["delta"] != 0.0
        assert result["buy_volume"] > 0.0
        assert result["sell_volume"] > 0.0
        assert len(result["footprint"]) > 0
        assert result["trade_count"] == 100

        # Verify VWAP is within reasonable range
        assert 99.0 <= result["vwap"] <= 101.0

        # Verify POC is within range of trades
        assert 99.5 <= result["poc"] <= 100.5

        # Verify delta makes sense
        total_volume = result["buy_volume"] + result["sell_volume"]
        assert abs(result["delta"]) <= total_volume

    def test_all_buys_scenario(self):
        """Test market scenario with all buy volume."""
        calc = MetricsCalculator(tick_size=0.1)
        trades = [
            _make_trade(price=100.0, qty=1.0, is_buyer_maker=False),
            _make_trade(price=100.1, qty=2.0, is_buyer_maker=False),
            _make_trade(price=100.2, qty=1.5, is_buyer_maker=False),
        ]
        result = calc.calculate(trades)

        assert result["delta"] == 4.5
        assert result["buy_volume"] == 4.5
        assert result["sell_volume"] == 0.0

    def test_all_sells_scenario(self):
        """Test market scenario with all sell volume."""
        calc = MetricsCalculator(tick_size=0.1)
        trades = [
            _make_trade(price=100.0, qty=1.0, is_buyer_maker=True),
            _make_trade(price=100.1, qty=2.0, is_buyer_maker=True),
            _make_trade(price=100.2, qty=1.5, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        assert result["delta"] == -4.5
        assert result["buy_volume"] == 0.0
        assert result["sell_volume"] == 4.5

    def test_high_volume_trades(self):
        """Test with high volume trades."""
        calc = MetricsCalculator(tick_size=0.01)
        trades = [
            _make_trade(price=100.0, qty=1000.0, is_buyer_maker=False),
            _make_trade(price=100.5, qty=500.0, is_buyer_maker=True),
        ]
        result = calc.calculate(trades)

        assert result["delta"] == 500.0
        assert result["buy_volume"] == 1000.0
        assert result["sell_volume"] == 500.0
        assert result["trade_count"] == 2


class TestMetricsPerformance:
    """Test performance characteristics."""

    def test_large_dataset_performance(self):
        """Test performance with larger dataset (1000 trades)."""
        calc = MetricsCalculator(tick_size=0.01)
        trades = []

        for i in range(1000):
            price = 100.0 + (i % 100) * 0.01
            qty = 0.5 + (i % 5) * 0.1
            trades.append(_make_trade(
                price=price,
                qty=qty,
                is_buyer_maker=i % 2 == 0,
            ))

        result = calc.calculate(trades)

        assert result["vwap"] is not None
        assert result["poc"] is not None
        assert result["trade_count"] == 1000
        assert len(result["footprint"]) > 0

    def test_calculation_idempotence(self):
        """Test that running calculate twice gives same results."""
        calc = MetricsCalculator(tick_size=0.1)
        trades = [
            _make_trade(price=100.0, qty=1.0, is_buyer_maker=False),
            _make_trade(price=100.5, qty=2.0, is_buyer_maker=True),
        ]

        result1 = calc.calculate(trades)
        result2 = calc.calculate(trades)

        assert result1["vwap"] == result2["vwap"]
        assert result1["poc"] == result2["poc"]
        assert result1["delta"] == result2["delta"]
        assert result1["footprint"] == result2["footprint"]
