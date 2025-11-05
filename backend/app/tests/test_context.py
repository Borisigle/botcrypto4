from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.context.service import ContextService, SymbolExchangeInfo
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
