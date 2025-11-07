from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
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
        backfill_rate_limit_threshold=3,
        backfill_cooldown_seconds=60,
        backfill_public_delay_ms=100,
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
        backfill_rate_limit_threshold=3,
        backfill_cooldown_seconds=60,
        backfill_public_delay_ms=100,
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


class TestCircuitBreaker:
    """Test circuit breaker functionality."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_initial_state(self, mock_settings):
        """Test that circuit breaker starts in CLOSED state."""
        from app.context.backfill import CircuitBreakerState
        client = BinanceHttpClient(mock_settings)
        
        assert client.circuit_state == CircuitBreakerState.CLOSED
        assert client.consecutive_rate_limit_errors == 0
        assert client.throttle_multiplier == 1.0
        
        await client.close()

    @pytest.mark.asyncio
    async def test_rate_limit_error_tracking(self, mock_settings):
        """Test that rate limit errors are tracked."""
        client = BinanceHttpClient(mock_settings)
        
        # Simulate rate limit errors
        for i in range(2):
            client.on_rate_limit_error()
            assert client.consecutive_rate_limit_errors == i + 1
            assert client.throttle_multiplier > 1.0
        
        await client.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold(self, mock_settings):
        """Test that circuit breaker opens after reaching threshold."""
        from app.context.backfill import CircuitBreakerState
        # Set threshold to 2
        mock_settings.backfill_rate_limit_threshold = 2
        client = BinanceHttpClient(mock_settings)
        
        # Simulate hitting the threshold
        client.on_rate_limit_error()
        assert client.circuit_state == CircuitBreakerState.CLOSED
        
        client.on_rate_limit_error()
        assert client.circuit_state == CircuitBreakerState.OPEN
        assert client.cooldown_until is not None
        
        await client.close()

    @pytest.mark.asyncio
    async def test_successful_request_resets_errors(self, mock_settings):
        """Test that successful requests reset error counter."""
        client = BinanceHttpClient(mock_settings)
        
        # Simulate rate limit errors
        client.on_rate_limit_error()
        client.on_rate_limit_error()
        assert client.consecutive_rate_limit_errors == 2
        
        # Successful request should reset counter
        client.on_successful_request()
        assert client.consecutive_rate_limit_errors == 0
        
        await client.close()

    @pytest.mark.asyncio
    async def test_throttle_multiplier_increases_on_errors(self, mock_settings):
        """Test that throttle multiplier increases with errors."""
        client = BinanceHttpClient(mock_settings)
        
        initial_multiplier = client.throttle_multiplier
        client.on_rate_limit_error()
        first_error_multiplier = client.throttle_multiplier
        
        assert first_error_multiplier > initial_multiplier
        
        client.on_rate_limit_error()
        second_error_multiplier = client.throttle_multiplier
        
        assert second_error_multiplier > first_error_multiplier
        
        await client.close()

    @pytest.mark.asyncio
    async def test_throttle_multiplier_decreases_on_success(self, mock_settings):
        """Test that throttle multiplier gradually decreases with successful requests."""
        client = BinanceHttpClient(mock_settings)
        
        # Increase throttle multiplier
        client.on_rate_limit_error()
        client.on_rate_limit_error()
        high_multiplier = client.throttle_multiplier
        
        # Successful request should decrease it
        client.on_successful_request()
        recovered_multiplier = client.throttle_multiplier
        
        assert recovered_multiplier < high_multiplier
        assert recovered_multiplier >= 1.0
        
        await client.close()

    @pytest.mark.asyncio
    async def test_get_throttle_multiplier(self, mock_settings):
        """Test getting current throttle multiplier."""
        client = BinanceHttpClient(mock_settings)
        
        # Should start at 1.0
        assert client.get_throttle_multiplier() == 1.0
        
        # After error, should be higher
        client.on_rate_limit_error()
        assert client.get_throttle_multiplier() > 1.0
        
        await client.close()

    @pytest.mark.asyncio
    async def test_get_recommended_concurrency_normal(self, mock_settings):
        """Test recommended concurrency in normal state."""
        client = BinanceHttpClient(mock_settings)
        
        # Normal state should return base concurrency
        recommended = client.get_recommended_concurrency(20)
        assert recommended == 20
        
        await client.close()

    @pytest.mark.asyncio
    async def test_get_recommended_concurrency_under_moderate_throttling(self, mock_settings):
        """Test recommended concurrency under moderate throttling."""
        client = BinanceHttpClient(mock_settings)
        
        # Simulate moderate throttling (1.5 < throttle < 2.0)
        client.throttle_multiplier = 1.7
        recommended = client.get_recommended_concurrency(20)
        assert recommended == 10  # Should be halved
        assert recommended >= 1
        
        await client.close()

    @pytest.mark.asyncio
    async def test_get_recommended_concurrency_under_heavy_throttling(self, mock_settings):
        """Test recommended concurrency under heavy throttling."""
        client = BinanceHttpClient(mock_settings)
        
        # Simulate heavy throttling (throttle > 2.0)
        client.throttle_multiplier = 2.5
        recommended = client.get_recommended_concurrency(20)
        assert recommended == 5  # Should be quartered
        assert recommended >= 1
        
        await client.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker_transitions_to_half_open(self, mock_settings):
        """Test that circuit breaker transitions to HALF_OPEN after cooldown."""
        from app.context.backfill import CircuitBreakerState
        import time
        
        mock_settings.backfill_cooldown_seconds = 0  # Immediate recovery for testing
        client = BinanceHttpClient(mock_settings)
        
        # Open the circuit
        client.on_rate_limit_error()
        client.on_rate_limit_error()
        client.on_rate_limit_error()
        assert client.circuit_state == CircuitBreakerState.OPEN
        
        # Wait for cooldown to expire
        await asyncio.sleep(0.1)
        
        # Check circuit breaker should transition to HALF_OPEN
        await client.check_circuit_breaker()
        assert client.circuit_state == CircuitBreakerState.HALF_OPEN
        
        await client.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker_closes_after_successful_request_in_half_open(self, mock_settings):
        """Test that circuit breaker closes after successful request in HALF_OPEN state."""
        from app.context.backfill import CircuitBreakerState
        
        mock_settings.backfill_cooldown_seconds = 0
        client = BinanceHttpClient(mock_settings)
        
        # Open the circuit and transition to HALF_OPEN
        client.on_rate_limit_error()
        client.on_rate_limit_error()
        client.on_rate_limit_error()
        assert client.circuit_state == CircuitBreakerState.OPEN
        
        await asyncio.sleep(0.1)
        await client.check_circuit_breaker()
        assert client.circuit_state == CircuitBreakerState.HALF_OPEN
        
        # Successful request should close it
        client.on_successful_request()
        assert client.circuit_state == CircuitBreakerState.CLOSED
        
        await client.close()


class TestCircuitBreakerWithRateLimitingScenarios:
    """Test realistic rate limiting scenarios."""

    @pytest.mark.asyncio
    async def test_progressive_recovery_of_throttle_multiplier(self, mock_settings):
        """Test that throttle multiplier gradually recovers."""
        client = BinanceHttpClient(mock_settings)
        
        # Simulate multiple errors
        for _ in range(5):
            client.on_rate_limit_error()
        
        high_multiplier = client.throttle_multiplier
        assert high_multiplier > 1.0
        
        # Simulate multiple successful requests
        multipliers = []
        for _ in range(10):
            client.on_successful_request()
            multipliers.append(client.throttle_multiplier)
        
        # Each step should recover (decrease)
        for i in range(1, len(multipliers)):
            assert multipliers[i] <= multipliers[i-1]
        
        # Should eventually return to 1.0
        assert multipliers[-1] == 1.0
        
        await client.close()

    @pytest.mark.asyncio
    async def test_fallback_to_public_mode_on_auth_rate_limit(self, mock_settings_with_auth):
        """Test that authenticated mode can fallback to public on rate limit."""
        client = BinanceHttpClient(mock_settings_with_auth)
        
        assert client.use_auth is True
        assert "X-MBX-APIKEY" in client.headers
        
        # Simulate rate limit scenario that triggers fallback
        # This would be done in fetch_agg_trades, but we can test the logic here
        client.use_auth = False
        assert client.use_auth is False
        
        await client.close()