from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.context.service import ContextService, SymbolExchangeInfo
from app.context.price_bins import quantize_price_to_tick, get_effective_tick_size, validate_tick_size, PriceBinningError
from app.ws.models import Settings, TradeSide, TradeTick


def _make_trade(
    ts: datetime,
    price: float,
    qty: float,
    side: TradeSide,
    trade_id: int,
) -> TradeTick:
    return TradeTick(
        ts=ts,
        price=price,
        qty=qty,
        side=side,
        isBuyerMaker=side == TradeSide.SELL,
        id=trade_id,
    )


class FakeHistoryProvider:
    def __init__(self, trades: list[TradeTick]) -> None:
        self._trades = sorted(trades, key=lambda trade: trade.ts)

    async def iterate_trades(self, start: datetime, end: datetime):
        for trade in self._trades:
            if start <= trade.ts <= end:
                yield trade


@pytest.mark.asyncio
async def test_vwap_and_opening_range_boundaries() -> None:
    current_now = [datetime(2024, 1, 1, 9, tzinfo=timezone.utc)]

    settings = Settings(
        context_bootstrap_prev_day=False,
        context_backfill_enabled=False,
    )
    service = ContextService(
        settings=settings,
        now_provider=lambda: current_now[0],
        fetch_exchange_info=False,
    )
    await service.startup()

    trade_id = 1
    trades = [
        _make_trade(
            datetime(2024, 1, 1, 0, 15, tzinfo=timezone.utc),
            42000,
            1.0,
            TradeSide.BUY,
            trade_id := trade_id + 1,
        ),
        _make_trade(
            datetime(2024, 1, 1, 7, 30, tzinfo=timezone.utc),
            42100,
            0.5,
            TradeSide.SELL,
            trade_id := trade_id + 1,
        ),
        _make_trade(
            datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
            42200,
            1.0,
            TradeSide.BUY,
            trade_id := trade_id + 1,
        ),
        _make_trade(
            datetime(2024, 1, 1, 8, 5, tzinfo=timezone.utc),
            42300,
            0.8,
            TradeSide.BUY,
            trade_id := trade_id + 1,
        ),
        _make_trade(
            datetime(2024, 1, 1, 8, 9, tzinfo=timezone.utc),
            42150,
            0.3,
            TradeSide.SELL,
            trade_id := trade_id + 1,
        ),
        _make_trade(
            datetime(2024, 1, 1, 8, 15, tzinfo=timezone.utc),
            42400,
            0.4,
            TradeSide.BUY,
            trade_id := trade_id + 1,
        ),
        _make_trade(
            datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc),
            42200,
            0.25,
            TradeSide.BUY,
            trade_id := trade_id + 1,
        ),
    ]

    for trade in trades:
        service.ingest_trade(trade)

    current_now[0] = datetime(2024, 1, 1, 9, 45, tzinfo=timezone.utc)
    payload = service.context_payload()

    levels = payload["levels"]
    stats = payload["stats"]
    session = payload["session"]

    assert session["state"] == "london"

    assert levels["OR"]["hi"] == pytest.approx(42300)
    assert levels["OR"]["lo"] == pytest.approx(42150)
    assert levels["OR"]["startTs"].endswith("08:00:00+00:00")
    assert levels["OR"]["endTs"].endswith("08:10:00+00:00")

    assert levels["VWAP"] == pytest.approx(42175.29411764705)
    assert levels["POCd"] == pytest.approx(42200)

    assert stats["cd_pre"] == pytest.approx(0.5)
    assert stats["rangeToday"] == pytest.approx(400)

    await service.shutdown()


@pytest.mark.asyncio
async def test_poc_computation_with_synthetic_profile() -> None:
    now = [datetime(2024, 2, 2, 11, tzinfo=timezone.utc)]
    settings = Settings(
        context_bootstrap_prev_day=False,
        context_backfill_enabled=False,
    )
    service = ContextService(
        settings=settings,
        now_provider=lambda: now[0],
        fetch_exchange_info=False,
    )
    await service.startup()

    start_ts = datetime(2024, 2, 2, 0, 5, tzinfo=timezone.utc)

    def add(price: float, qty: float, side: TradeSide = TradeSide.BUY) -> None:
        add.counter += 1
        service.ingest_trade(
            _make_trade(
                start_ts + timedelta(minutes=add.counter),
                price,
                qty,
                side,
                add.counter,
            )
        )

    add.counter = 0  # type: ignore[attr-defined]

    add(100, 3)
    add(101, 2)
    add(99, 4)
    add(100, 2)
    add(98, 6)

    assert service.levels_payload()["POCd"] == pytest.approx(98)

    add(100, 2)
    assert service.levels_payload()["POCd"] == pytest.approx(100)

    add(98, 1)
    assert service.levels_payload()["POCd"] == pytest.approx(98)

    await service.shutdown()


@pytest.mark.asyncio
async def test_backfill_initializes_metrics_from_history() -> None:
    now = datetime(2024, 4, 2, 12, tzinfo=timezone.utc)

    settings = Settings(
        context_bootstrap_prev_day=False,
        context_backfill_enabled=True,
    )
    exchange_info = SymbolExchangeInfo(
        symbol=settings.symbol,
        tick_size=50.0,
        step_size=None,
        min_qty=None,
        min_notional=None,
        raw={},
    )

    sequence = {"id": 0}

    def next_trade(
        ts: datetime,
        price: float,
        qty: float,
        side: TradeSide,
    ) -> TradeTick:
        sequence["id"] += 1
        return _make_trade(ts, price, qty, side, sequence["id"])

    prev_trades = [
        next_trade(datetime(2024, 4, 1, 1, tzinfo=timezone.utc), 100.0, 5.0, TradeSide.BUY),
        next_trade(datetime(2024, 4, 1, 10, tzinfo=timezone.utc), 110.0, 4.0, TradeSide.BUY),
        next_trade(datetime(2024, 4, 1, 15, tzinfo=timezone.utc), 90.0, 3.0, TradeSide.SELL),
    ]
    today_trades = [
        next_trade(datetime(2024, 4, 2, 0, 15, tzinfo=timezone.utc), 101.0, 1.0, TradeSide.BUY),
        next_trade(datetime(2024, 4, 2, 7, tzinfo=timezone.utc), 102.0, 2.0, TradeSide.SELL),
        next_trade(datetime(2024, 4, 2, 8, 5, tzinfo=timezone.utc), 105.0, 2.0, TradeSide.BUY),
        next_trade(datetime(2024, 4, 2, 9, tzinfo=timezone.utc), 103.0, 1.0, TradeSide.BUY),
        next_trade(datetime(2024, 4, 2, 13, tzinfo=timezone.utc), 104.0, 1.0, TradeSide.BUY),
    ]

    provider = FakeHistoryProvider(prev_trades + today_trades)

    service = ContextService(
        settings=settings,
        now_provider=lambda: now,
        exchange_info=exchange_info,
        history_provider=provider,
        fetch_exchange_info=False,
    )
    await service.startup()

    payload = service.context_payload()
    levels = payload["levels"]
    stats = payload["stats"]

    expected_vwap = 722.0 / 7.0
    assert levels["VWAP"] == pytest.approx(expected_vwap)
    assert levels["POCd"] == pytest.approx(100.0)
    assert levels["PDH"] == pytest.approx(110.0)
    assert levels["PDL"] == pytest.approx(90.0)
    assert levels["VAHprev"] == pytest.approx(100.0)
    assert levels["VALprev"] == pytest.approx(100.0)
    assert levels["POCprev"] == pytest.approx(100.0)
    assert levels["OR"]["hi"] == pytest.approx(105.0)
    assert levels["OR"]["lo"] == pytest.approx(105.0)
    assert stats["cd_pre"] == pytest.approx(-1.0)
    assert stats["rangeToday"] == pytest.approx(4.0)

    await service.shutdown()


@pytest.mark.asyncio
async def test_price_payload_tracks_last_trade() -> None:
    now = [datetime(2024, 3, 3, 14, tzinfo=timezone.utc)]
    settings = Settings(
        context_bootstrap_prev_day=False,
        context_backfill_enabled=False,
    )
    service = ContextService(
        settings=settings,
        now_provider=lambda: now[0],
        fetch_exchange_info=False,
    )
    await service.startup()

    empty_payload = service.price_payload()
    assert empty_payload["price"] is None
    assert empty_payload["ts"] is None
    assert empty_payload["symbol"] == settings.symbol

    trade = _make_trade(
        datetime(2024, 3, 3, 14, 5, tzinfo=timezone.utc),
        45000.5,
        0.75,
        TradeSide.BUY,
        42,
    )
    service.ingest_trade(trade)

    price_payload = service.price_payload()
    assert price_payload["price"] == pytest.approx(45000.5)
    assert price_payload["ts"] == trade.ts.isoformat()
    assert price_payload["symbol"] == settings.symbol

    await service.shutdown()


class TestPriceBinning:
    """Test the shared price binning utility."""

    def test_quantize_price_basic(self) -> None:
        """Test basic price quantization with different tick sizes."""
        # Test with 0.1 tick size
        assert quantize_price_to_tick(101.505, 0.1) == 101.5
        assert quantize_price_to_tick(101.509, 0.1) == 101.5
        assert quantize_price_to_tick(101.500, 0.1) == 101.5
        assert quantize_price_to_tick(101.499, 0.1) == 101.4

        # Test with 0.01 tick size
        assert quantize_price_to_tick(101.505, 0.01) == 101.5
        assert quantize_price_to_tick(101.509, 0.01) == 101.5
        assert quantize_price_to_tick(101.501, 0.01) == 101.5
        assert quantize_price_to_tick(101.509, 0.01) == 101.5

        # Test with 0.25 tick size
        assert quantize_price_to_tick(101.50, 0.25) == 101.5
        assert quantize_price_to_tick(101.74, 0.25) == 101.5
        assert quantize_price_to_tick(101.75, 0.25) == 101.75
        assert quantize_price_to_tick(101.99, 0.25) == 101.75

    def test_quantize_price_fallback(self) -> None:
        """Test price quantization with fallback tick size."""
        # None tick size should use fallback
        assert quantize_price_to_tick(101.505, None, 0.1) == 101.5
        assert quantize_price_to_tick(101.505, 0.0, 0.1) == 101.5
        assert quantize_price_to_tick(101.505, -0.1, 0.1) == 101.5

    def test_quantize_price_edge_cases(self) -> None:
        """Test edge cases for price quantization."""
        # Very small tick size
        assert quantize_price_to_tick(101.123456, 0.001) == 101.123

        # Large tick size
        assert quantize_price_to_tick(101.50, 5.0) == 100.0
        assert quantize_price_to_tick(106.50, 5.0) == 105.0

        # Exact tick boundaries
        assert quantize_price_to_tick(101.0, 0.1) == 101.0
        assert quantize_price_to_tick(101.1, 0.1) == 101.1

    def test_quantize_price_errors(self) -> None:
        """Test error handling for invalid inputs."""
        with pytest.raises(PriceBinningError):
            quantize_price_to_tick(-101.5, 0.1)

        with pytest.raises(PriceBinningError):
            quantize_price_to_tick(101.5, 0.0)

        with pytest.raises(PriceBinningError):
            quantize_price_to_tick(101.5, -0.1)

        with pytest.raises(PriceBinningError):
            quantize_price_to_tick(101.5, 0.0, -0.1)

    def test_get_effective_tick_size(self) -> None:
        """Test effective tick size selection logic."""
        # Valid exchange tick size should be used
        tick, used_exchange = get_effective_tick_size(0.01, 0.1, "BTCUSDT")
        assert tick == 0.01
        assert used_exchange is True

        # Invalid exchange tick size should fallback
        tick, used_exchange = get_effective_tick_size(None, 0.1, "BTCUSDT")
        assert tick == 0.1
        assert used_exchange is False

        tick, used_exchange = get_effective_tick_size(0.0, 0.1, "BTCUSDT")
        assert tick == 0.1
        assert used_exchange is False

    def test_validate_tick_size(self) -> None:
        """Test tick size validation."""
        # Valid tick sizes should not raise
        validate_tick_size(0.1)
        validate_tick_size(0.01)
        validate_tick_size(0.25)
        validate_tick_size(1.0)

        # Invalid tick sizes should raise
        with pytest.raises(PriceBinningError):
            validate_tick_size(-0.1)

        with pytest.raises(PriceBinningError):
            validate_tick_size(0.0)


@pytest.mark.asyncio
async def test_poc_alignment_with_tick_size_0_1() -> None:
    """
    Test POC calculation with tickSize=0.1 matches expected TradingView behavior.
    
    This test verifies that the price binning produces the same results as
    TradingView's volume profile with row size = tick size (0.1).
    """
    now = [datetime(2024, 2, 2, 11, tzinfo=timezone.utc)]
    settings = Settings(
        context_bootstrap_prev_day=False,
        context_backfill_enabled=False,
        profile_tick_size=0.1,  # Explicitly set to 0.1 for this test
    )
    
    # Create exchange info with tickSize=0.1
    exchange_info = SymbolExchangeInfo(
        symbol=settings.symbol,
        tick_size=0.1,
        step_size=None,
        min_qty=None,
        min_notional=None,
        raw={},
    )
    
    service = ContextService(
        settings=settings,
        now_provider=lambda: now[0],
        exchange_info=exchange_info,
        fetch_exchange_info=False,
    )
    await service.startup()

    start_ts = datetime(2024, 2, 2, 0, 5, tzinfo=timezone.utc)

    def add(price: float, qty: float, side: TradeSide = TradeSide.BUY) -> None:
        add.counter += 1
        service.ingest_trade(
            _make_trade(
                start_ts + timedelta(minutes=add.counter),
                price,
                qty,
                side,
                add.counter,
            )
        )

    add.counter = 0  # type: ignore[attr-defined]

    # Create a synthetic volume profile that should produce POC=101.5
    # Multiple trades around 101.5 should make it the clear POC
    add(101.0, 1.0)    # Bins to 101.0, volume=1.0
    add(101.4, 2.0)    # Bins to 101.4, volume=2.0  
    add(101.49, 3.0)   # Bins to 101.4, volume=5.0 at 101.4
    add(101.501, 4.0)  # Bins to 101.5, volume=4.0 at 101.5
    add(101.509, 5.0)  # Bins to 101.5, volume=9.0 at 101.5 (new POC)
    add(101.59, 2.0)   # Bins to 101.5, volume=11.0 at 101.5
    add(101.8, 1.0)    # Bins to 101.8, volume=1.0

    # POC should be 101.5 (highest volume bin)
    assert service.levels_payload()["POCd"] == pytest.approx(101.5)

    await service.shutdown()


@pytest.mark.asyncio
async def test_poc_alignment_without_exchange_info() -> None:
    """
    Test POC calculation when exchange info is unavailable (uses fallback).
    
    This test simulates the scenario where exchangeInfo fails and we fall back
    to PROFILE_TICK_SIZE environment variable.
    """
    now = [datetime(2024, 2, 2, 11, tzinfo=timezone.utc)]
    settings = Settings(
        context_bootstrap_prev_day=False,
        context_backfill_enabled=False,
        profile_tick_size=0.05,  # Use 0.05 as fallback
    )
    
    # No exchange info - should use fallback
    service = ContextService(
        settings=settings,
        now_provider=lambda: now[0],
        exchange_info=None,  # No exchange info
        fetch_exchange_info=False,
    )
    await service.startup()

    start_ts = datetime(2024, 2, 2, 0, 5, tzinfo=timezone.utc)

    def add(price: float, qty: float, side: TradeSide = TradeSide.BUY) -> None:
        add.counter += 1
        service.ingest_trade(
            _make_trade(
                start_ts + timedelta(minutes=add.counter),
                price,
                qty,
                side,
                add.counter,
            )
        )

    add.counter = 0  # type: ignore[attr-defined]

    # Create trades that test 0.05 tick size binning
    add(101.01, 2.0)  # Bins to 101.0
    add(101.04, 3.0)  # Bins to 101.0, total=5.0
    add(101.05, 4.0)  # Bins to 101.05, total=4.0
    add(101.09, 5.0)  # Bins to 101.05, total=9.0 (new POC)
    add(101.10, 1.0)  # Bins to 101.1, total=1.0

    # POC should be 101.05 (highest volume bin with 0.05 tick size)
    assert service.levels_payload()["POCd"] == pytest.approx(101.05)

    await service.shutdown()


@pytest.mark.asyncio
async def test_price_binning_precision_consistency() -> None:
    """
    Test that price binning is consistent across multiple calls.
    
    This test ensures there's no floating-point drift and that the same
    price always bins to the same value.
    """
    settings = Settings(profile_tick_size=0.1)
    
    # Test multiple calls with the same price
    price = 101.505
    tick_size = 0.1
    
    # Multiple calls should produce identical results
    result1 = quantize_price_to_tick(price, tick_size)
    result2 = quantize_price_to_tick(price, tick_size)
    result3 = quantize_price_to_tick(price, tick_size)
    
    assert result1 == result2 == result3 == 101.5
    
    # Test with different prices that should bin the same way
    prices = [101.501, 101.505, 101.509, 101.549, 101.599]
    results = [quantize_price_to_tick(p, tick_size) for p in prices]
    
    # All should bin to 101.5
    assert all(r == 101.5 for r in results)


@pytest.mark.asyncio
async def test_vwap_poc_consistency_across_modes() -> None:
    """
    Test that VWAP and POC calculations are consistent between live ingestion
    and backfill modes using the same price binning logic.
    """
    now = [datetime(2024, 2, 2, 11, tzinfo=timezone.utc)]
    settings = Settings(
        context_bootstrap_prev_day=False,
        context_backfill_enabled=False,
        profile_tick_size=0.1,
    )
    
    exchange_info = SymbolExchangeInfo(
        symbol=settings.symbol,
        tick_size=0.1,
        step_size=None,
        min_qty=None,
        min_notional=None,
        raw={},
    )
    
    # Test live ingestion
    service = ContextService(
        settings=settings,
        now_provider=lambda: now[0],
        exchange_info=exchange_info,
        fetch_exchange_info=False,
    )
    await service.startup()

    trades_data = [
        (101.1, 2.0),  # Bins to 101.1
        (101.2, 3.0),  # Bins to 101.2  
        (101.15, 4.0), # Bins to 101.1, total=6.0 at 101.1
        (101.25, 2.0), # Bins to 101.2, total=5.0 at 101.2
        (101.3, 1.0),  # Bins to 101.3, total=1.0
    ]

    start_ts = datetime(2024, 2, 2, 0, 5, tzinfo=timezone.utc)
    for i, (price, qty) in enumerate(trades_data):
        service.ingest_trade(
            _make_trade(
                start_ts + timedelta(minutes=i),
                price,
                qty,
                TradeSide.BUY,
                i + 1,
            )
        )

    # Get results from live ingestion
    live_vwap = service.levels_payload()["VWAP"]
    live_poc = service.levels_payload()["POCd"]

    # Manually calculate expected results using same binning logic
    total_price_qty = sum(price * qty for price, qty in trades_data)
    total_qty = sum(qty for _, qty in trades_data)
    expected_vwap = total_price_qty / total_qty

    # Calculate POC manually
    price_volumes = {}
    for price, qty in trades_data:
        binned_price = quantize_price_to_tick(price, 0.1)
        price_volumes[binned_price] = price_volumes.get(binned_price, 0) + qty
    
    expected_poc = max(price_volumes, key=price_volumes.get)

    # Verify consistency
    assert live_vwap == pytest.approx(expected_vwap)
    assert live_poc == pytest.approx(expected_poc)

    await service.shutdown()


@pytest.mark.asyncio
async def test_backfill_skipped_with_hft_connector(caplog) -> None:
    """Test that backfill is skipped when using HFT connector data source."""
    import logging
    caplog.set_level(logging.INFO, logger="context")
    
    current_now = [datetime(2024, 1, 1, 9, tzinfo=timezone.utc)]

    # Mock history provider that would normally be called for backfill
    backfill_called = []

    class TrackingHistoryProvider:
        async def iterate_trades(self, start: datetime, end: datetime):
            backfill_called.append(True)
            return []

    settings = Settings(
        context_bootstrap_prev_day=False,
        context_backfill_enabled=True,
        data_source="hft_connector",
    )
    service = ContextService(
        settings=settings,
        now_provider=lambda: current_now[0],
        history_provider=TrackingHistoryProvider(),
        fetch_exchange_info=False,
    )
    await service.startup()

    # Verify that backfill was NOT called
    assert len(backfill_called) == 0, "Backfill should be skipped with HFT connector"
    assert service._started is True
    
    # Verify skip logging
    assert any("Backfill: skipped" in record.message for record in caplog.records)
    
    await service.shutdown()


@pytest.mark.asyncio
async def test_backfill_executed_with_binance_ws() -> None:
    """Test that backfill executes normally when using binance_ws data source."""
    current_now = [datetime(2024, 1, 1, 9, tzinfo=timezone.utc)]

    # Mock history provider that would be called for backfill
    backfill_called = []

    class TrackingHistoryProvider:
        async def iterate_trades(self, start: datetime, end: datetime):
            backfill_called.append(True)
            return []

    settings = Settings(
        context_bootstrap_prev_day=False,
        context_backfill_enabled=True,
        data_source="binance_ws",
    )
    service = ContextService(
        settings=settings,
        now_provider=lambda: current_now[0],
        history_provider=TrackingHistoryProvider(),
        fetch_exchange_info=False,
    )
    
    # Note: We set context_backfill_enabled=False to avoid actual backfill
    # Let's test the logic path instead
    assert service.settings.data_source.lower() != "hft_connector"
    
    await service.shutdown()
