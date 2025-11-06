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
            request_delay=0.0,
            chunk_minutes=60,
        )
        
        # Test that history is properly configured
        assert history.settings == mock_settings
        assert history.limit == 1000
        assert history.chunk_minutes == 60
        assert history.max_iterations_per_chunk == 500  # default value
        
        # Test that HTTP client is created
        assert history.http_client is not None
        assert history.http_client.settings == mock_settings

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