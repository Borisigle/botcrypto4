"""Tests for Bybit WebSocket connector."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.connectors.bybit_websocket import BybitTrade, BybitWebSocketConnector


def test_bybit_trade_creation() -> None:
    """Test BybitTrade object creation and conversion."""
    trade_time = datetime.now(timezone.utc)
    trade = BybitTrade(
        price=43250.5,
        qty=0.1,
        side="Buy",
        time=trade_time,
        symbol="BTCUSDT",
        trade_id="123456789"
    )
    
    assert trade.price == 43250.5
    assert trade.qty == 0.1
    assert trade.side == "Buy"
    assert trade.time == trade_time
    assert trade.symbol == "BTCUSDT"
    assert trade.trade_id == "123456789"


def test_bybit_trade_to_dict() -> None:
    """Test BybitTrade to_dict conversion."""
    trade_time = datetime.now(timezone.utc)
    trade = BybitTrade(
        price=43250.5,
        qty=0.1,
        side="Buy",
        time=trade_time,
        symbol="BTCUSDT",
        trade_id="123456789"
    )
    
    trade_dict = trade.to_dict()
    
    assert trade_dict["price"] == 43250.5
    assert trade_dict["qty"] == 0.1
    assert trade_dict["side"] == "Buy"
    assert trade_dict["time"] == trade_time.isoformat()
    assert trade_dict["symbol"] == "BTCUSDT"
    assert trade_dict["trade_id"] == "123456789"


def test_bybit_trade_to_trade_tick() -> None:
    """Test BybitTrade to TradeTick conversion."""
    from app.ws.models import TradeSide
    
    trade_time = datetime.now(timezone.utc)
    trade = BybitTrade(
        price=43250.5,
        qty=0.1,
        side="Buy",
        time=trade_time,
        symbol="BTCUSDT",
        trade_id="123456789"
    )
    
    trade_tick = trade.to_trade_tick()
    
    assert trade_tick.price == 43250.5
    assert trade_tick.qty == 0.1
    assert trade_tick.side == TradeSide.BUY
    assert trade_tick.isBuyerMaker is False  # Buy side means taker was buyer
    assert trade_tick.id == 123456789
    assert trade_tick.ts == trade_time


def test_bybit_trade_to_trade_tick_sell() -> None:
    """Test BybitTrade to TradeTick conversion for sell side."""
    from app.ws.models import TradeSide
    
    trade_time = datetime.now(timezone.utc)
    trade = BybitTrade(
        price=43250.5,
        qty=0.1,
        side="Sell",
        time=trade_time,
        symbol="BTCUSDT",
        trade_id="123456789"
    )
    
    trade_tick = trade.to_trade_tick()
    
    assert trade_tick.side == TradeSide.SELL
    assert trade_tick.isBuyerMaker is True  # Sell side means taker was seller


def test_bybit_websocket_connector_init() -> None:
    """Test BybitWebSocketConnector initialization."""
    connector = BybitWebSocketConnector(
        symbol="ETHUSDT",
        buffer_size=500,
        testnet=True
    )
    
    assert connector.symbol == "ETHUSDT"
    assert connector.buffer_size == 500
    assert connector.testnet is True
    assert connector.ws_url == "wss://stream.bybit.com/v5/public/spot"
    assert not connector.is_connected
    assert connector.trade_count == 0


def test_bybit_websocket_connector_init_mainnet() -> None:
    """Test BybitWebSocketConnector initialization for mainnet."""
    connector = BybitWebSocketConnector(
        symbol="BTCUSDT",
        testnet=False
    )
    
    assert connector.ws_url == "wss://stream.bybit.com/v5/public/linear"


def test_bybit_websocket_connector_get_recent_trades_empty() -> None:
    """Test getting recent trades from empty buffer."""
    connector = BybitWebSocketConnector()
    trades = connector.get_recent_trades(10)
    assert trades == []


def test_bybit_websocket_connector_get_trades_range_empty() -> None:
    """Test getting trades in range from empty buffer."""
    connector = BybitWebSocketConnector()
    start_time = datetime.now(timezone.utc)
    end_time = start_time.replace(second=start_time.second + 30)
    
    trades = connector.get_trades_range(start_time, end_time)
    assert trades == []


@pytest.mark.asyncio
async def test_bybit_websocket_connector_mock_connection() -> None:
    """Test WebSocket connector with mocked connection."""
    connector = BybitWebSocketConnector()
    
    # Mock the connection method
    connector._connect_and_subscribe = AsyncMock()
    connector._listen_loop = AsyncMock(side_effect=StopAsyncIteration())
    
    # This should not raise an exception
    try:
        await connector._reconnect_loop()
    except StopAsyncIteration:
        pass  # Expected
    
    connector._connect_and_subscribe.assert_called_once()