from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.context.backfill import BinanceTradeHistory
from app.ws.models import Settings, TradeSide


@pytest.mark.asyncio
async def test_binance_trade_history_handles_pagination() -> None:
    start_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(milliseconds=2)
    start_ms = int(start_dt.timestamp() * 1000)

    batches = {
        start_ms: [
            {"a": 1, "p": "100.0", "q": "0.1", "T": start_ms, "m": False},
            {"a": 2, "p": "101.0", "q": "0.2", "T": start_ms + 1, "m": True},
        ],
        start_ms + 2: [
            {"a": 3, "p": "102.0", "q": "0.3", "T": start_ms + 2, "m": False},
        ],
    }

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start_time = int(request.url.params["startTime"])
        calls.append(start_time)
        data = batches.get(start_time, [])
        return httpx.Response(200, json=data)

    transport = httpx.MockTransport(handler)

    # Use small chunk size to force single-threaded for this test
    history = BinanceTradeHistory(
        settings=Settings(context_backfill_enabled=True),
        limit=2,
        request_delay=0.0,
        chunk_minutes=60,  # Large chunk to avoid parallelization for this test
        http_client_factory=lambda: httpx.AsyncClient(transport=transport),
    )

    trades = []
    async for trade in history.iterate_trades(start_dt, end_dt):
        trades.append(trade)

    assert [trade.id for trade in trades] == [1, 2, 3]
    assert trades[0].price == pytest.approx(100.0)
    assert trades[1].side is TradeSide.SELL
    assert trades[2].side is TradeSide.BUY
    assert calls == [start_ms, start_ms + 2]


@pytest.mark.asyncio
async def test_binance_trade_history_parallel_deduplication() -> None:
    """Test that parallel backfill deduplicates trades correctly."""
    start_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(hours=1)  # 1 hour window to trigger parallelization
    
    # Simulate overlapping trades between chunks (common at boundaries)
    batches = {
        0: [
            {"a": 1, "p": "100.0", "q": "0.1", "T": 1000, "m": False},
            {"a": 2, "p": "101.0", "q": "0.2", "T": 2000, "m": True},
        ],
        1800000: [  # 30 minutes later
            {"a": 2, "p": "101.0", "q": "0.2", "T": 2000, "m": True},  # Duplicate!
            {"a": 3, "p": "102.0", "q": "0.3", "T": 3000, "m": False},
        ],
    }

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start_time = int(request.url.params["startTime"])
        calls.append(start_time)
        data = batches.get(start_time, [])
        return httpx.Response(200, json=data)

    transport = httpx.MockTransport(handler)

    # Use small chunk size to force parallelization
    history = BinanceTradeHistory(
        settings=Settings(context_backfill_enabled=True),
        limit=2,
        request_delay=0.0,
        chunk_minutes=30,  # 30-minute chunks
        max_concurrent_chunks=2,
        http_client_factory=lambda: httpx.AsyncClient(transport=transport),
    )

    trades = []
    async for trade in history.iterate_trades(start_dt, end_dt):
        trades.append(trade)

    # Should have 3 unique trades (duplicate trade ID 2 should be removed)
    assert [trade.id for trade in trades] == [1, 2, 3]
    assert len(trades) == 3
    
    # Trades should be sorted by timestamp
    assert trades[0].ts < trades[1].ts < trades[2].ts


@pytest.mark.asyncio
async def test_binance_trade_history_safety_limit() -> None:
    """Test that safety limit prevents infinite pagination loops."""
    start_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(hours=1)
    start_ms = int(start_dt.timestamp() * 1000)

    # Mock infinite pagination: always return same batch (cursor never advances)
    infinite_batch = [
        {"a": 1, "p": "100.0", "q": "0.1", "T": start_ms, "m": False},
        {"a": 2, "p": "101.0", "q": "0.2", "T": start_ms + 1, "m": True},
    ]

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start_time = int(request.url.params["startTime"])
        calls.append(start_time)
        return httpx.Response(200, json=infinite_batch)

    transport = httpx.MockTransport(handler)

    history = BinanceTradeHistory(
        settings=Settings(context_backfill_enabled=True),
        limit=2,
        request_delay=0.0,
        max_iterations_per_chunk=5,  # Very low limit for testing
        chunk_minutes=60,  # Single chunk to test iteration limit
        http_client_factory=lambda: httpx.AsyncClient(transport=transport),
    )

    trades = []
    async for trade in history.iterate_trades(start_dt, end_dt):
        trades.append(trade)

    # Should stop after safety limit, even though pagination would continue
    assert len(calls) == 5  # max_iterations_per_chunk
    # Should have trades from first 4 iterations (5th hits limit and breaks)
    assert len(trades) == 8  # 2 trades * 4 successful iterations
