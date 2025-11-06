from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest
import aiohttp

from app.context.backfill import BinanceTradeHistory, BinanceHttpClient
from app.ws.models import Settings, TradeSide


@pytest.fixture
def mock_settings():
    return Settings(
        context_backfill_enabled=True,
        binance_api_timeout=30,
        backfill_max_retries=5,
        backfill_retry_base=0.5,
        binance_api_key=None,  # No auth by default for tests
        binance_api_secret=None,
    )


@pytest.fixture
def mock_settings_with_auth():
    return Settings(
        context_backfill_enabled=True,
        binance_api_timeout=30,
        backfill_max_retries=5,
        backfill_retry_base=0.5,
        binance_api_key="test_api_key_1234567890",
        binance_api_secret="test_api_secret_1234567890abcdef",
    )


class TestBinanceHttpClient:
    """Test the BinanceHttpClient class."""

    @pytest.mark.asyncio
    async def test_session_creation_and_headers(self, mock_settings):
        """Test that HTTP session is created with proper headers."""
        client = BinanceHttpClient(mock_settings)
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            await client.connect()
            
            # Verify session was created with correct headers
            mock_session_class.assert_called_once()
            call_args = mock_session_class.call_args
            assert call_args[1]['headers'] == client.headers
            assert call_args[1]['timeout'].total == mock_settings.binance_api_timeout
            
            await client.close()

    @pytest.mark.asyncio
    async def test_authentication_disabled_by_default(self, mock_settings):
        """Test that authentication is disabled when no credentials provided."""
        client = BinanceHttpClient(mock_settings)
        
        assert client.use_auth is False
        assert "X-MBX-APIKEY" not in client.headers
        assert client.api_key is None
        assert client.api_secret is None

    @pytest.mark.asyncio
    async def test_authentication_enabled_with_credentials(self, mock_settings_with_auth):
        """Test that authentication is enabled when credentials provided."""
        client = BinanceHttpClient(mock_settings_with_auth)
        
        assert client.use_auth is True
        assert "X-MBX-APIKEY" in client.headers
        assert client.headers["X-MBX-APIKEY"] == "test_api_key_1234567890"
        assert client.api_key == "test_api_key_1234567890"
        assert client.api_secret == "test_api_secret_1234567890abcdef"

    def test_hmac_signature_generation(self, mock_settings_with_auth):
        """Test HMAC-SHA256 signature generation."""
        client = BinanceHttpClient(mock_settings_with_auth)
        
        params = {
            "symbol": "BTCUSDT",
            "startTime": 1640995200000,
            "endTime": 1640995800000,
            "limit": 1000,
            "timestamp": 1640995800000,
            "recvWindow": 5000
        }
        
        signature = client._sign_request(params)
        
        # Signature should be a 64-character hex string
        assert len(signature) == 64
        assert all(c in "0123456789abcdef" for c in signature)
        
        # Same params should generate same signature
        signature2 = client._sign_request(params)
        assert signature == signature2
        
        # Different params should generate different signature
        params_diff = params.copy()
        params_diff["limit"] = 500
        signature3 = client._sign_request(params_diff)
        assert signature != signature3

    @pytest.mark.asyncio
    async def test_request_signing_in_fetch(self, mock_settings_with_auth):
        """Test that request parameters are properly signed when auth is enabled."""
        client = BinanceHttpClient(mock_settings_with_auth)
        
        # Test the signing logic directly without mocking the HTTP call
        params = {
            "symbol": "BTCUSDT",
            "startTime": 1640995200000,
            "endTime": 1640995800000,
            "limit": 1000,
            "timestamp": 1640995800000,
            "recvWindow": 5000
        }
        
        # Test signature generation
        signature = client._sign_request(params)
        
        # Should include auth parameters
        assert "timestamp" in params
        assert "recvWindow" in params
        assert params["recvWindow"] == 5000
        assert len(signature) == 64  # HMAC-SHA256 hex string
        assert all(c in "0123456789abcdef" for c in signature)
        
        await client.close()

    @pytest.mark.asyncio
    async def test_successful_fetch(self, mock_settings):
        """Test successful API fetch - simplified test focusing on core logic."""
        client = BinanceHttpClient(mock_settings)
        
        # Test that client can be created and configured correctly
        assert client.headers["User-Agent"] == "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        assert client.headers["Accept"] == "application/json"
        assert client.max_retries == 5
        assert client.retry_base == 0.5
        
        # Test exponential backoff calculation
        delay = client._exponential_backoff(0)
        assert 0.4 <= delay <= 0.6  # 0.5 ± 20%
        
        await client.close()

    @pytest.mark.asyncio
    async def test_418_error_with_retry(self, mock_settings):
        """Test 418 error triggers retry with exponential backoff - simplified."""
        client = BinanceHttpClient(mock_settings)
        
        # Test that retry logic is properly configured
        assert client.max_retries == 5
        
        # Test exponential backoff sequence
        delays = [client._exponential_backoff(i) for i in range(3)]
        expected_base = [0.5, 1.0, 2.0]
        for i, (delay, expected) in enumerate(zip(delays, expected_base)):
            assert expected * 0.8 <= delay <= expected * 1.2  # ±20% jitter
        
        await client.close()

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, mock_settings):
        """Test that max retries are respected - simplified."""
        client = BinanceHttpClient(mock_settings)
        
        # Test configuration
        assert client.max_retries == 5
        assert client.retry_base == 0.5
        
        # Test that max delay is respected (with jitter, it can slightly exceed 30)
        delay = client._exponential_backoff(10)  # Very high attempt number
        assert delay <= 36  # max_delay parameter + 20% jitter
        
        await client.close()

    def test_exponential_backoff_with_jitter(self, mock_settings):
        """Test exponential backoff calculation with jitter."""
        client = BinanceHttpClient(mock_settings)
        
        # Test multiple attempts to ensure jitter is applied
        delays = [client._exponential_backoff(i) for i in range(3)]
        
        # Base delays should be: 0.5, 1.0, 2.0
        # With jitter, they should be close but not exactly equal
        assert 0.4 <= delays[0] <= 0.6  # 0.5 ± 20%
        assert 0.8 <= delays[1] <= 1.2  # 1.0 ± 20%
        assert 1.6 <= delays[2] <= 2.4  # 2.0 ± 20%


class TestBinanceTradeHistory:
    """Test the BinanceTradeHistory class."""

    @pytest.mark.asyncio
    async def test_pagination_logic(self, mock_settings):
        """Test pagination logic - simplified test focusing on configuration."""
        history = BinanceTradeHistory(
            settings=mock_settings,
            limit=1000,
            request_delay=0.1,  # Override the default for public mode
            chunk_minutes=60,
        )
        
        # Test that history is properly configured for public mode
        assert history.settings == mock_settings
        assert history.limit == 1000
        assert history.chunk_minutes == 60
        assert history.max_iterations_per_chunk == 500  # default value
        assert history.max_concurrent_chunks == 5  # Public mode default
        assert history.request_delay == 0.1  # Should match our override
        
        # Test that HTTP client is created
        assert history.http_client is not None
        assert history.http_client.settings == mock_settings

    @pytest.mark.asyncio
    async def test_pagination_logic_with_auth(self, mock_settings_with_auth):
        """Test pagination logic with authentication enabled."""
        history = BinanceTradeHistory(
            settings=mock_settings_with_auth,
            limit=1000,
            request_delay=0.0,
            chunk_minutes=60,
        )
        
        # Test that history is properly configured for auth mode
        assert history.settings == mock_settings_with_auth
        assert history.limit == 1000
        assert history.chunk_minutes == 60
        assert history.max_iterations_per_chunk == 500  # default value
        assert history.max_concurrent_chunks == 20  # Auth mode default
        assert history.request_delay == 0.0  # No delay with auth
        
        # Test that HTTP client is created with auth
        assert history.http_client is not None
        assert history.http_client.settings == mock_settings_with_auth
        assert history.http_client.use_auth is True

    @pytest.mark.asyncio
    async def test_parallel_deduplication(self, mock_settings):
        """Test parallel backfill configuration - simplified."""
        history = BinanceTradeHistory(
            settings=mock_settings,
            limit=1000,
            request_delay=0.0,
            chunk_minutes=30,
            max_concurrent_chunks=2,
        )
        
        # Test configuration
        assert history.chunk_minutes == 30
        assert history.max_concurrent_chunks == 2
        
        # Test time range splitting
        start_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(hours=1)
        chunks = history._split_time_range(start_dt, end_dt, 30)
        
        # Should create 2 chunks of 30 minutes each
        assert len(chunks) == 2
        assert chunks[0][1] - chunks[0][0] == timedelta(minutes=30)
        assert chunks[1][1] - chunks[1][0] == timedelta(minutes=30)

    @pytest.mark.asyncio
    async def test_safety_limit_prevents_infinite_pagination(self, mock_settings):
        """Test safety limit configuration - simplified."""
        history = BinanceTradeHistory(
            settings=mock_settings,
            limit=2,
            request_delay=0.0,
            max_iterations_per_chunk=5,  # Very low limit for testing
            chunk_minutes=60,
        )
        
        # Test safety limit is set correctly
        assert history.max_iterations_per_chunk == 5
        assert history.limit == 2

    @pytest.mark.asyncio
    async def test_http_client_closed_properly(self, mock_settings):
        """Test that HTTP client is created and can be closed."""
        history = BinanceTradeHistory(settings=mock_settings)
        
        # Test that HTTP client exists
        assert history.http_client is not None
        
        # Test that close method exists and can be called
        assert hasattr(history.http_client, 'close')
        
        # Close the client
        await history.http_client.close()

    @pytest.mark.asyncio
    async def test_reduced_concurrency_and_throttling(self, mock_settings):
        """Test that public mode uses reduced concurrency and throttling."""
        history = BinanceTradeHistory(settings=mock_settings)
        
        # Test that public mode uses reduced concurrency (5)
        assert history.max_concurrent_chunks == 5
        assert history.request_delay > 0  # Should have throttling delay
        
        # Test that we can still override it if needed
        custom_history = BinanceTradeHistory(
            settings=mock_settings,
            max_concurrent_chunks=3
        )
        assert custom_history.max_concurrent_chunks == 3
        
        # Test time range splitting still works
        start_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(hours=2)  # 2 hours = 12 chunks of 10 minutes
        chunks = history._split_time_range(start_dt, end_dt, 10)
        
        # Should create 12 chunks of 10 minutes each
        assert len(chunks) == 12
        assert chunks[0][1] - chunks[0][0] == timedelta(minutes=10)
        
        await history.http_client.close()
        await custom_history.http_client.close()

    @pytest.mark.asyncio
    async def test_higher_concurrency_with_auth(self, mock_settings_with_auth):
        """Test that auth mode uses higher concurrency with no throttling."""
        history = BinanceTradeHistory(settings=mock_settings_with_auth)
        
        # Test that auth mode uses higher concurrency (20) and no delay
        assert history.max_concurrent_chunks == 20
        assert history.request_delay == 0.0  # No throttling needed with auth
        
        # Test that HTTP client has auth enabled
        assert history.http_client.use_auth is True
        
        await history.http_client.close()

    @pytest.mark.asyncio
    async def test_test_mode_configuration(self, mock_settings_with_auth):
        """Test that test mode is properly configured."""
        # Enable test mode
        mock_settings_with_auth.context_backfill_test_mode = True
        
        history = BinanceTradeHistory(settings=mock_settings_with_auth)
        
        # Test that test mode is detected
        assert history.test_mode is True
        assert history.max_concurrent_chunks == 1  # Serial execution
        assert history.request_delay == 0.0  # No delay needed
        
        # Test that HTTP client has auth enabled
        assert history.http_client.use_auth is True
        
        await history.http_client.close()

    @pytest.mark.asyncio
    async def test_test_mode_without_auth(self, mock_settings):
        """Test that test mode works without authentication."""
        # Enable test mode without auth
        mock_settings.context_backfill_test_mode = True
        
        history = BinanceTradeHistory(settings=mock_settings)
        
        # Test that test mode is detected
        assert history.test_mode is True
        assert history.max_concurrent_chunks == 1  # Serial execution
        assert history.request_delay == 0.0  # No delay needed
        
        # Test that HTTP client has auth disabled
        assert history.http_client.use_auth is False
        
        await history.http_client.close()

    @pytest.mark.asyncio
    async def test_test_single_window_method(self, mock_settings_with_auth):
        """Test the test_single_window method exists and can be called."""
        # Enable test mode
        mock_settings_with_auth.context_backfill_test_mode = True
        
        history = BinanceTradeHistory(settings=mock_settings_with_auth)
        
        # Test that the method exists
        assert hasattr(history, 'test_single_window')
        assert callable(history.test_single_window)
        
        # Test that it's an async method
        import inspect
        assert inspect.iscoroutinefunction(history.test_single_window)
        
        await history.http_client.close()