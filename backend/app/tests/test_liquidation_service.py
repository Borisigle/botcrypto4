"""Tests for liquidation service with Binance API."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.liquidation_service import LiquidationService


@pytest.fixture
def liquidation_service():
    """Create a LiquidationService instance."""
    return LiquidationService(
        symbol="BTCUSDT",
        limit=200,
        bin_size=100.0,
        max_clusters=20,
        base_url="https://fapi.binance.com",
    )


def test_liquidation_service_init(liquidation_service: LiquidationService) -> None:
    """Test LiquidationService initialization."""
    assert liquidation_service.symbol == "BTCUSDT"
    assert liquidation_service.limit == 200
    assert liquidation_service.bin_size == 100.0
    assert liquidation_service.max_clusters == 20
    assert liquidation_service.endpoint == "https://fapi.binance.com/fapi/v1/forceOrders"
    assert liquidation_service.liquidations == []
    assert liquidation_service.clusters == {}


def test_normalize_liquidation_binance_format() -> None:
    """Test normalization of Binance force order format."""
    entry = {
        "symbol": "BTCUSDT",
        "price": "50000.5",
        "origQty": "1.5",
        "side": "SELL"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is not None
    assert result["price"] == 50000.5
    assert result["qty"] == 1.5
    assert result["side"] == "sell"


def test_normalize_liquidation_with_qty_fallback() -> None:
    """Test normalization with qty as fallback."""
    entry = {
        "symbol": "BTCUSDT",
        "price": "50000.5",
        "qty": "1.5",
        "side": "BUY"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is not None
    assert result["price"] == 50000.5
    assert result["qty"] == 1.5
    assert result["side"] == "buy"


def test_normalize_liquidation_invalid_qty() -> None:
    """Test normalization with invalid quantity."""
    entry = {
        "symbol": "BTCUSDT",
        "price": "50000.5",
        "origQty": "0",
        "side": "SELL"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is None


def test_normalize_liquidation_invalid_side() -> None:
    """Test normalization with invalid side."""
    entry = {
        "symbol": "BTCUSDT",
        "price": "50000.5",
        "origQty": "1.5",
        "side": "INVALID"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is None


def test_normalize_liquidation_non_numeric_price() -> None:
    """Test normalization with non-numeric price."""
    entry = {
        "symbol": "BTCUSDT",
        "price": "invalid",
        "origQty": "1.5",
        "side": "SELL"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is None


@pytest.mark.asyncio
async def test_fetch_liquidations_success(liquidation_service: LiquidationService) -> None:
    """Test successful liquidation fetch from Binance."""
    mock_response_data = [
        {"symbol": "BTCUSDT", "price": "91500", "origQty": "10.5", "side": "SELL"},
        {"symbol": "BTCUSDT", "price": "91500.5", "origQty": "5.2", "side": "BUY"},
        {"symbol": "BTCUSDT", "price": "91600", "origQty": "8.3", "side": "SELL"},
    ]
    
    mock_response = MagicMock()
    mock_response.json.return_value = mock_response_data
    mock_response.raise_for_status = MagicMock()
    
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    with patch("app.services.liquidation_service.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None
        
        await liquidation_service.fetch_liquidations()
    
    assert len(liquidation_service.liquidations) == 3
    assert liquidation_service.last_updated is not None


@pytest.mark.asyncio
async def test_fetch_liquidations_http_error(liquidation_service: LiquidationService) -> None:
    """Test liquidation fetch with HTTP error."""
    import httpx
    
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection error"))
    
    with patch("app.services.liquidation_service.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None
        
        await liquidation_service.fetch_liquidations()
    
    assert len(liquidation_service.liquidations) == 0
    assert liquidation_service.last_updated is None


@pytest.mark.asyncio
async def test_fetch_liquidations_empty_response(liquidation_service: LiquidationService) -> None:
    """Test liquidation fetch with empty response."""
    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()
    
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    with patch("app.services.liquidation_service.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None
        
        await liquidation_service.fetch_liquidations()
    
    assert len(liquidation_service.liquidations) == 0


def test_build_clusters(liquidation_service: LiquidationService) -> None:
    """Test cluster building with mock liquidations."""
    liquidation_service.liquidations = [
        {"price": 91500.0, "qty": 10.5, "side": "sell"},
        {"price": 91500.5, "qty": 5.2, "side": "buy"},
        {"price": 91600.0, "qty": 8.3, "side": "sell"},
    ]
    
    liquidation_service._build_clusters_locked()
    
    assert len(liquidation_service.clusters) > 0
    
    for price_level, bucket in liquidation_service.clusters.items():
        assert "buy" in bucket
        assert "sell" in bucket
        assert "total" in bucket
        assert "ratio" in bucket


def test_get_nearest_support(liquidation_service: LiquidationService) -> None:
    """Test support level calculation."""
    liquidation_service.clusters = {
        91400.0: {"buy": 10.0, "sell": 5.0, "total": 15.0, "ratio": 2.0},
        91500.0: {"buy": 15.0, "sell": 10.0, "total": 25.0, "ratio": 1.5},
        91600.0: {"buy": 5.0, "sell": 20.0, "total": 25.0, "ratio": 0.25},
    }
    
    support = liquidation_service.get_nearest_support(91550.0)
    
    assert support == 91500.0


def test_get_nearest_resistance(liquidation_service: LiquidationService) -> None:
    """Test resistance level calculation."""
    liquidation_service.clusters = {
        91400.0: {"buy": 10.0, "sell": 5.0, "total": 15.0, "ratio": 2.0},
        91500.0: {"buy": 15.0, "sell": 10.0, "total": 25.0, "ratio": 1.5},
        91600.0: {"buy": 5.0, "sell": 20.0, "total": 25.0, "ratio": 0.25},
    }
    
    resistance = liquidation_service.get_nearest_resistance(91550.0)
    
    assert resistance == 91600.0


def test_get_clusters_sorted(liquidation_service: LiquidationService) -> None:
    """Test that clusters are returned sorted by total volume."""
    liquidation_service.clusters = {
        91400.0: {"buy": 10.0, "sell": 5.0, "total": 15.0, "ratio": 2.0},
        91500.0: {"buy": 50.0, "sell": 30.0, "total": 80.0, "ratio": 1.67},
        91600.0: {"buy": 5.0, "sell": 20.0, "total": 25.0, "ratio": 0.25},
    }
    
    clusters = liquidation_service.get_clusters()
    
    cluster_list = list(clusters.values())
    assert cluster_list[0]["total"] == 80.0


def test_endpoint_construction() -> None:
    """Test that endpoint is constructed correctly for Binance."""
    service = LiquidationService(
        base_url="https://fapi.binance.com",
        symbol="BTCUSDT"
    )
    
    assert service.endpoint == "https://fapi.binance.com/fapi/v1/forceOrders"


def test_endpoint_construction_with_trailing_slash() -> None:
    """Test endpoint construction handles trailing slash."""
    service = LiquidationService(
        base_url="https://fapi.binance.com/",
        symbol="BTCUSDT"
    )
    
    assert service.endpoint == "https://fapi.binance.com/fapi/v1/forceOrders"


def test_liquidation_service_with_authentication() -> None:
    """Test LiquidationService initialization with API credentials."""
    service = LiquidationService(
        symbol="BTCUSDT",
        api_key="test_api_key",
        api_secret="test_api_secret"
    )
    
    assert service.signer is not None
    assert service.signer.api_key == "test_api_key"
    assert service.signer.api_secret == "test_api_secret"


def test_liquidation_service_without_authentication() -> None:
    """Test LiquidationService initialization without API credentials."""
    service = LiquidationService(symbol="BTCUSDT")
    
    assert service.signer is None


@pytest.mark.asyncio
async def test_fetch_liquidations_with_authentication() -> None:
    """Test that authenticated requests include proper headers and signatures."""
    service = LiquidationService(
        symbol="BTCUSDT",
        api_key="test_api_key",
        api_secret="test_api_secret"
    )
    
    mock_response_data = [
        {"symbol": "BTCUSDT", "price": "91500", "origQty": "10.5", "side": "SELL"},
    ]
    
    mock_response = MagicMock()
    mock_response.json.return_value = mock_response_data
    mock_response.raise_for_status = MagicMock()
    
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    with patch("app.services.liquidation_service.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None
        
        await service.fetch_liquidations()
    
    # Verify authentication was applied
    assert mock_client.get.called
    call_kwargs = mock_client.get.call_args.kwargs
    
    # Check headers include API key
    assert "headers" in call_kwargs
    assert call_kwargs["headers"]["X-MBX-APIKEY"] == "test_api_key"
    
    # Check params include signature and timestamp
    params = call_kwargs["params"]
    assert "signature" in params
    assert "timestamp" in params
    assert params["symbol"] == "BTCUSDT"
