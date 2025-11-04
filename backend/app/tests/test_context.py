from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.context.service import ContextService
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


@pytest.mark.asyncio
async def test_vwap_and_opening_range_boundaries() -> None:
    current_now = [datetime(2024, 1, 1, 9, tzinfo=timezone.utc)]

    settings = Settings(context_bootstrap_prev_day=False)
    service = ContextService(settings=settings, now_provider=lambda: current_now[0])
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
    settings = Settings(context_bootstrap_prev_day=False)
    service = ContextService(settings=settings, now_provider=lambda: now[0])
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
