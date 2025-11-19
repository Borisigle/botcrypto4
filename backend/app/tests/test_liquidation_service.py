"""Tests for liquidation service with Coinglass API."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.liquidation_service import LiquidationService


@pytest.fixture
def liquidation_service():
    """Create a LiquidationService instance."""
    return LiquidationService(
        symbol="BTC",
        limit=200,
        bin_size=100.0,
        max_clusters=20,
        base_url="https://open-api.coinglass.com",
    )


def test_liquidation_service_init(liquidation_service: LiquidationService) -> None:
    """Test LiquidationService initialization."""
    assert liquidation_service.symbol == "BTC"
    assert liquidation_service.limit == 200
    assert liquidation_service.bin_size == 100.0
    assert liquidation_service.max_clusters == 20
    assert liquidation_service.endpoint == "https://open-api.coinglass.com/public/v2/liquidation/latest"
    assert liquidation_service.liquidations == []
    assert liquidation_service.clusters == {}


def test_normalize_liquidation_coinglass_format() -> None:
    """Test normalization of Coinglass liquidation format."""
    entry = {
        "symbol": "BTC",
        "price": "50000.5",
        "amount": "1.5",
        "type": "short"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is not None
    assert result["price"] == 50000.5
    assert result["qty"] == 1.5
    assert result["side"] == "sell"


def test_normalize_liquidation_coinglass_long() -> None:
    """Test normalization of long liquidation (buy)."""
    entry = {
        "symbol": "BTC",
        "price": "50000.5",
        "amount": "1.5",
        "type": "long"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is not None
    assert result["price"] == 50000.5
    assert result["qty"] == 1.5
    assert result["side"] == "buy"


def test_normalize_liquidation_invalid_qty() -> None:
    """Test normalization with invalid quantity."""
    entry = {
        "symbol": "BTC",
        "price": "50000.5",
        "amount": "0",
        "type": "short"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is None


def test_normalize_liquidation_invalid_side() -> None:
    """Test normalization with invalid type."""
    entry = {
        "symbol": "BTC",
        "price": "50000.5",
        "amount": "1.5",
        "type": "invalid"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is None


def test_normalize_liquidation_non_numeric_price() -> None:
    """Test normalization with non-numeric price."""
    entry = {
        "symbol": "BTC",
        "price": "invalid",
        "amount": "1.5",
        "type": "short"
    }
    
    result = LiquidationService._normalize_liquidation(entry)
    
    assert result is None


@pytest.mark.asyncio
async def test_fetch_liquidations_success(liquidation_service: LiquidationService) -> None:
    """Test successful liquidation fetch from Coinglass."""
    mock_response_data = {
        "code": "0",
        "msg": "success",
        "data": [
            {"symbol": "BTC", "price": "91500", "amount": "10.5", "type": "short"},
            {"symbol": "BTC", "price": "91500.5", "amount": "5.2", "type": "long"},
            {"symbol": "BTC", "price": "91600", "amount": "8.3", "type": "short"},
        ]
    }
    
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
    mock_response.json.return_value = {"code": "0", "msg": "success", "data": []}
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
    """Test that endpoint is constructed correctly for Coinglass."""
    service = LiquidationService(
        base_url="https://open-api.coinglass.com",
        symbol="BTC"
    )
    
    assert service.endpoint == "https://open-api.coinglass.com/public/v2/liquidation/latest"


def test_endpoint_construction_with_trailing_slash() -> None:
    """Test endpoint construction handles trailing slash."""
    service = LiquidationService(
        base_url="https://open-api.coinglass.com/",
        symbol="BTC"
    )
    
    assert service.endpoint == "https://open-api.coinglass.com/public/v2/liquidation/latest"


def test_liquidation_service_ignores_authentication() -> None:
    """Test LiquidationService ignores API credentials (Coinglass is public)."""
    service = LiquidationService(
        symbol="BTC",
        api_key="test_api_key",
        api_secret="test_api_secret"
    )
    
    assert not hasattr(service, 'signer') or service.signer is None


def test_liquidation_service_no_authentication_needed() -> None:
    """Test LiquidationService works without API credentials (Coinglass is public)."""
    service = LiquidationService(symbol="BTC")
    
    assert not hasattr(service, 'signer') or service.signer is None


@pytest.mark.asyncio
async def test_fetch_liquidations_public_api() -> None:
    """Test that public requests include User-Agent header."""
    service = LiquidationService(symbol="BTC")
    
    mock_response_data = {
        "code": "0",
        "msg": "success",
        "data": [
            {"symbol": "BTC", "price": "91500", "amount": "10.5", "type": "short"},
        ]
    }
    
    mock_response = MagicMock()
    mock_response.json.return_value = mock_response_data
    mock_response.raise_for_status = MagicMock()
    
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    with patch("app.services.liquidation_service.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None
        
        await service.fetch_liquidations()
    
    # Verify public request includes User-Agent
    assert mock_client.get.called
    call_kwargs = mock_client.get.call_args.kwargs
    
    # Check params include symbol and limit
    params = call_kwargs["params"]
    assert "symbol" in params
    assert "limit" in params
    assert params["symbol"] == "BTC"
    
    # Check headers include User-Agent
    headers = call_kwargs.get("headers", {})
    assert "User-Agent" in headers
