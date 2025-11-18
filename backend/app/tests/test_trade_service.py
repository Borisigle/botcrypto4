"""Tests for trade service."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.services.trade_service import TradeService
from app.ws.models import Settings


def test_trade_service_init() -> None:
    """Test TradeService initialization."""
    settings = Settings()
    service = TradeService(settings)
    
    assert service._buffer_size == settings.max_queue
    assert not service.is_bybit_connected
    assert service.get_recent_trades(10) == []


@pytest.mark.asyncio
async def test_trade_service_add_trade() -> None:
    """Test adding trade to service."""
    settings = Settings()
    service = TradeService(settings)
    
    trade_data = {
        "price": 43250.5,
        "qty": 0.1,
        "side": "Buy",
        "time": datetime.now(timezone.utc).isoformat(),
        "symbol": "BTCUSDT",
        "trade_id": "123456789"
    }
    
    await service.add_trade(trade_data)
    
    trades = service.get_recent_trades(1)
    assert len(trades) == 1
    assert trades[0]["price"] == 43250.5
    assert trades[0]["qty"] == 0.1
    assert trades[0]["side"] == "Buy"


def test_trade_service_get_stats_empty() -> None:
    """Test getting stats from empty service."""
    settings = Settings()
    service = TradeService(settings)
    
    stats = service.get_stats()
    
    assert stats["total_count"] == 0
    assert stats["oldest_trade_time"] is None
    assert stats["newest_trade_time"] is None
    assert stats["bybit_connected"] is False


def test_trade_service_get_trades_range() -> None:
    """Test getting trades in time range."""
    settings = Settings()
    service = TradeService(settings)
    
    # Add some test trades
    base_time = datetime.now(timezone.utc)
    trade1 = {
        "price": 43250.0,
        "time": base_time.isoformat(),
    }
    trade2 = {
        "price": 43251.0,
        "time": base_time.replace(second=base_time.second + 10).isoformat(),
    }
    trade3 = {
        "price": 43252.0,
        "time": base_time.replace(second=base_time.second + 20).isoformat(),
    }
    
    # Add trades directly to buffer for testing
    service._trades_buffer.extend([trade1, trade2, trade3])
    
    # Test range query
    start_time = base_time.replace(second=base_time.second + 5)
    end_time = base_time.replace(second=base_time.second + 15)
    
    trades = service.get_trades_range(start_time, end_time)
    
    # Should return only trade2 (within range)
    assert len(trades) == 1
    assert trades[0]["price"] == 43251.0


@pytest.mark.asyncio
async def test_trade_service_start_stop_bybit() -> None:
    """Test starting and stopping Bybit connector."""
    settings = Settings()
    service = TradeService(settings)
    
    # Mock the connector
    mock_connector = AsyncMock()
    service._bybit_connector = mock_connector
    
    await service.stop_bybit_connector()
    mock_connector.disconnect.assert_called_once()
    
    # Test with None connector
    service._bybit_connector = None
    await service.start_bybit_connector()
    await service.stop_bybit_connector()
    
    # Should not raise any exceptions
    assert True