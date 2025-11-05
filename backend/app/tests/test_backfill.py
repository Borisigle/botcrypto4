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

    history = BinanceTradeHistory(
        settings=Settings(context_backfill_enabled=True),
        limit=2,
        request_delay=0.0,
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
