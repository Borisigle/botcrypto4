from datetime import datetime, timezone

import pytest

from app.ws.models import TradeSide
from app.ws.trades import parse_trade_message


def test_parse_trade_message_normalizes_fields() -> None:
    payload = {
        "e": "aggTrade",
        "E": 1717440000000,
        "T": 1717440001234,
        "a": 42,
        "p": "68000.5",
        "q": "0.25",
        "m": True,
    }

    tick = parse_trade_message(payload)

    assert tick.id == 42
    assert tick.price == pytest.approx(68000.5)
    assert tick.qty == pytest.approx(0.25)
    assert tick.side is TradeSide.SELL
    assert tick.isBuyerMaker is True
    assert tick.ts == datetime.fromtimestamp(1717440001234 / 1000, tz=timezone.utc)


def test_parse_trade_message_falls_back_to_event_time() -> None:
    payload = {
        "e": "trade",
        "E": 1717440005678,
        "t": 99,
        "p": "100.0",
        "q": "1.0",
        "m": False,
    }

    tick = parse_trade_message(payload)

    assert tick.ts == datetime.fromtimestamp(1717440005678 / 1000, tz=timezone.utc)
    assert tick.side is TradeSide.BUY
    assert tick.id == 99


def test_parse_trade_message_missing_required_fields_raises() -> None:
    with pytest.raises(ValueError):
        parse_trade_message({"p": "1.0"})
