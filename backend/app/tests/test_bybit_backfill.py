from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import json
import pytest
import aiohttp

from app.context.backfill import BybitConnectorHistory, BybitHttpClient
from app.ws.models import Settings, TradeSide


@pytest.fixture
def mock_settings():
    return Settings(
        context_backfill_enabled=True,
        bybit_api_timeout=30,
        bybit_backfill_max_retries=5,
        bybit_backfill_retry_base=0.5,
        bybit_api_key=None,  # No auth by default for tests
        bybit_api_secret=None,
        bybit_backfill_rate_limit_threshold=3,
        bybit_backfill_cooldown_seconds=60,
        bybit_backfill_public_delay_ms=50,
        bybit_backfill_max_concurrent_chunks=8,
        backfill_cache_enabled=False,
    )


@pytest.fixture
def mock_settings_with_auth():
    return Settings(
        context_backfill_enabled=True,
        bybit_api_timeout=30,
        bybit_backfill_max_retries=5,
        bybit_backfill_retry_base=0.5,
        bybit_api_key="test_bybit_api_key_1234567890",
        bybit_api_secret="test_bybit_api_secret_1234567890abcdef",
        bybit_backfill_rate_limit_threshold=3,
        bybit_backfill_cooldown_seconds=60,
        bybit_backfill_public_delay_ms=50,
        bybit_backfill_max_concurrent_chunks=8,
        backfill_cache_enabled=False,
    )


class TestBybitHttpClient:
    """Test the BybitHttpClient class."""

    @pytest.mark.asyncio
    async def test_session_creation_and_headers(self, mock_settings):
        """Test that HTTP session is created with proper headers."""
        client = BybitHttpClient(mock_settings)
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            await client.connect()
            
            # Verify session was created with correct headers
            mock_session_class.assert_called_once()
            call_args = mock_session_class.call_args
            assert call_args[1]['headers'] == client.headers
            assert call_args[1]['timeout'].total == mock_settings.bybit_api_timeout
            
            await client.close()

    @pytest.mark.asyncio
    async def test_authentication_disabled_by_default(self, mock_settings):
        """Test that authentication is disabled when no credentials provided."""
        client = BybitHttpClient(mock_settings)
        
        assert client.use_auth is False
        assert "X-BAPI-API-KEY" not in client.headers
        assert client.api_key is None
        assert client.api_secret is None

    @pytest.mark.asyncio
    async def test_authentication_enabled_with_credentials(self, mock_settings_with_auth):
        """Test that authentication is enabled when credentials provided."""
        client = BybitHttpClient(mock_settings_with_auth)
        
        assert client.use_auth is True
        assert "X-BAPI-API-KEY" in client.headers
        assert client.headers["X-BAPI-API-KEY"] == "test_bybit_api_key_1234567890"
        assert client.api_key == "test_bybit_api_key_1234567890"
        assert client.api_secret == "test_bybit_api_secret_1234567890abcdef"

    def test_hmac_signature_generation(self, mock_settings_with_auth):
        """Test HMAC-SHA256 signature generation."""
        client = BybitHttpClient(mock_settings_with_auth)
        
        params = {
            "category": "linear",
            "symbol": "BTCUSDT",
            "limit": 1000,
            "startTime": 1640995200000,
            "endTime": 1640995800000,
        }
        
        signature, timestamp, recv_window = client._sign_request(params)
        
        assert isinstance(signature, str)
        assert len(signature) == 64  # SHA256 hex length
        assert isinstance(timestamp, str)
        assert isinstance(recv_window, int)
        assert recv_window == 5000

    @pytest.mark.asyncio
    async def test_fetch_public_trades_success(self, mock_settings):
        """Test successful public trades fetch."""
        client = BybitHttpClient(mock_settings)
        
        mock_response_data = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "execId": "test_trade_1",
                        "symbol": "BTCUSDT",
                        "price": "50000.0",
                        "size": "0.1",
                        "side": "Buy",
                        "time": "1640995200000",
                        "isBlockTrade": False
                    }
                ]
            }
        }
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_session.get.return_value.__aenter__.return_value = mock_response
            mock_session_class.return_value = mock_session
            
            await client.connect()
            result = await client.fetch_public_trades("BTCUSDT", 1640995200000, 1640995800000)
            
            assert len(result) == 1
            assert result[0]["execId"] == "test_trade_1"
            assert result[0]["price"] == "50000.0"
            assert result[0]["side"] == "Buy"

    @pytest.mark.asyncio
    async def test_fetch_private_trades_auth_error_fallback(self, mock_settings_with_auth):
        """Test fallback to public trades on authentication error."""
        client = BybitHttpClient(mock_settings_with_auth)
        
        # First call returns 401, second call (public) succeeds
        mock_private_response = AsyncMock()
        mock_private_response.status = 401
        
        mock_public_response_data = {
            "retCode": 0,
            "retMsg": "OK", 
            "result": {"list": []}
        }
        mock_public_response = AsyncMock()
        mock_public_response.status = 200
        mock_public_response.json = AsyncMock(return_value=mock_public_response_data)
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session.get.return_value.__aenter__.side_effect = [mock_private_response, mock_public_response]
            mock_session_class.return_value = mock_session
            
            await client.connect()
            result = await client.fetch_private_trades("BTCUSDT", 1640995200000, 1640995800000)
            
            # Should fallback to public mode
            assert client.use_auth is False
            assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_circuit_breaker_state_management(self, mock_settings):
        """Test circuit breaker state transitions."""
        client = BybitHttpClient(mock_settings)
        
        # Initially closed
        assert client.circuit_state.value == "closed"
        assert client.consecutive_rate_limit_errors == 0
        
        # Trigger rate limit error
        client.on_rate_limit_error()
        assert client.consecutive_rate_limit_errors == 1
        assert client.throttle_multiplier > 1.0
        
        # Trigger enough errors to open circuit
        for _ in range(2):  # Total 3 errors (threshold is 3)
            client.on_rate_limit_error()
        
        assert client.circuit_state.value == "open"
        assert client.cooldown_until is not None
        
        # Successful request should reset in half-open state
        client.circuit_state = client.circuit_state.__class__.HALF_OPEN
        client.on_successful_request()
        assert client.circuit_state.value == "closed"
        assert client.consecutive_rate_limit_errors == 0


class TestBybitConnectorHistory:
    """Test the BybitConnectorHistory class."""

    @pytest.mark.asyncio
    async def test_initialization_without_auth(self, mock_settings):
        """Test initialization without authentication."""
        history = BybitConnectorHistory(mock_settings)
        
        assert history.settings == mock_settings
        assert history.http_client.use_auth is False
        assert history.max_concurrent_chunks == 4  # Half of default 8 for public mode
        assert history.request_delay == 0.05  # 50ms converted to seconds

    @pytest.mark.asyncio
    async def test_initialization_with_auth(self, mock_settings_with_auth):
        """Test initialization with authentication."""
        history = BybitConnectorHistory(mock_settings_with_auth)
        
        assert history.settings == mock_settings_with_auth
        assert history.http_client.use_auth is True
        assert history.max_concurrent_chunks == 8  # Full concurrency for auth mode
        assert history.request_delay == 0.0  # No delay for auth mode

    @pytest.mark.asyncio
    async def test_parse_bybit_public_trade(self, mock_settings):
        """Test parsing of Bybit public trade format."""
        history = BybitConnectorHistory(mock_settings)
        
        raw_trade = {
            "execId": "test_trade_123",
            "symbol": "BTCUSDT",
            "price": "50000.0",
            "size": "0.1",
            "side": "Buy",
            "time": "1640995200000",
            "isBlockTrade": False
        }
        
        trade = history._parse_bybit_trade(raw_trade)
        
        assert trade.price == 50000.0
        assert trade.qty == 0.1
        assert trade.side == "buy"
        assert trade.isBuyerMaker is False  # Buy side = taker, not maker
        assert trade.ts == datetime.fromtimestamp(1640995200000 / 1000, tz=timezone.utc)

    @pytest.mark.asyncio
    async def test_parse_bybit_private_trade(self, mock_settings):
        """Test parsing of Bybit private trade format."""
        history = BybitConnectorHistory(mock_settings)
        
        raw_trade = {
            "symbol": "BTCUSDT",
            "execId": "test_private_trade_456",
            "orderLinkId": "order_123",
            "orderId": "order_456",
            "side": "Sell",
            "orderPrice": "50100.0",
            "orderQty": "0.2",
            "execType": "Trade",
            "execQty": "0.15",
            "execPrice": "50100.0",
            "execFee": "0.75",
            "execTime": "1640995300000",
            "leavesQty": "0.05",
            "closedSize": "0.0"
        }
        
        trade = history._parse_bybit_trade(raw_trade)
        
        assert trade.price == 50100.0
        assert trade.qty == 0.15  # execQty, not orderQty
        assert trade.side == "sell"
        assert trade.isBuyerMaker is True  # Sell side = maker
        assert trade.ts == datetime.fromtimestamp(1640995300000 / 1000, tz=timezone.utc)

    @pytest.mark.asyncio
    async def test_trade_tick_conversion_roundtrip(self, mock_settings):
        """Test conversion between TradeTick and dict formats."""
        history = BybitConnectorHistory(mock_settings)
        
        # Create original trade
        original_trade = history._parse_bybit_trade({
            "execId": "test_trade_789",
            "symbol": "BTCUSDT",
            "price": "50200.0",
            "size": "0.3",
            "side": "Buy",
            "time": "1640995400000",
            "isBlockTrade": False
        })
        
        # Convert to dict and back
        trade_dict = history._trade_tick_to_dict(original_trade)
        restored_trade = history._dicts_to_trade_ticks([trade_dict])[0]
        
        assert restored_trade.price == original_trade.price
        assert restored_trade.qty == original_trade.qty
        assert restored_trade.side == original_trade.side
        assert restored_trade.isBuyerMaker == original_trade.isBuyerMaker
        assert restored_trade.ts == original_trade.ts

    @pytest.mark.asyncio
    async def test_split_time_range(self, mock_settings):
        """Test time range splitting into chunks."""
        history = BybitConnectorHistory(mock_settings)
        
        start_dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(2025, 1, 1, 1, 0, 0, tzinfo=timezone.utc)  # 1 hour
        
        chunks = history._split_time_range(start_dt, end_dt, 10)  # 10-minute chunks
        
        assert len(chunks) == 6  # 60 minutes / 10 minutes = 6 chunks
        
        # Check first chunk
        assert chunks[0][0] == start_dt
        assert chunks[0][1] == start_dt + timedelta(minutes=10)
        
        # Check last chunk
        assert chunks[-1][0] == start_dt + timedelta(minutes=50)
        assert chunks[-1][1] == end_dt

    @pytest.mark.asyncio
    async def test_fetch_trades_paginated(self, mock_settings):
        """Test paginated trade fetching."""
        history = BybitConnectorHistory(mock_settings)
        
        # Mock HTTP client response
        mock_response_data = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "execId": "trade_1",
                        "price": "50000.0",
                        "size": "0.1",
                        "side": "Buy",
                        "time": "1640995200000"
                    },
                    {
                        "execId": "trade_2", 
                        "price": "50001.0",
                        "size": "0.2",
                        "side": "Sell",
                        "time": "1640995201000"
                    }
                ]
            }
        }
        
        with patch.object(history.http_client, 'fetch_public_trades', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_response_data["result"]["list"]
            
            trades = await history._fetch_trades_paginated(
                datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 1, 1, 0, 10, 0, tzinfo=timezone.utc)
            )
            
            assert len(trades) == 2
            assert trades[0].price == 50000.0
            assert trades[0].side == "buy"
            assert trades[1].price == 50001.0
            assert trades[1].side == "sell"

    @pytest.mark.asyncio
    async def test_test_single_window(self, mock_settings):
        """Test single window test mode."""
        history = BybitConnectorHistory(mock_settings)
        history.test_mode = True
        
        with patch.object(history, '_fetch_trades_paginated', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [
                history._parse_bybit_trade({
                    "execId": "test_trade",
                    "price": "50000.0",
                    "size": "0.1", 
                    "side": "Buy",
                    "time": "1640995200000"
                })
            ]
            
            trades = await history.test_single_window()
            
            assert len(trades) == 1
            assert trades[0].price == 50000.0
            mock_fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_iterate_trades_small_window(self, mock_settings):
        """Test iterate_trades for small time windows."""
        history = BybitConnectorHistory(mock_settings)
        
        start_dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(2025, 1, 1, 0, 20, 0, tzinfo=timezone.utc)  # 20 minutes
        
        with patch.object(history, '_fetch_trades_paginated', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = []
            
            trades = []
            async for trade in history.iterate_trades(start_dt, end_dt):
                trades.append(trade)
            
            mock_fetch.assert_called_once()
            assert isinstance(trades, list)

    @pytest.mark.asyncio
    async def test_iterate_trades_large_window(self, mock_settings):
        """Test iterate_trades for large time windows requiring parallel processing."""
        history = BybitConnectorHistory(mock_settings)
        
        start_dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(2025, 1, 1, 2, 0, 0, tzinfo=timezone.utc)  # 2 hours
        
        with patch.object(history, '_backfill_parallel', new_callable=AsyncMock) as mock_backfill:
            mock_backfill.return_value = []
            
            trades = []
            async for trade in history.iterate_trades(start_dt, end_dt):
                trades.append(trade)
            
            mock_backfill.assert_called_once()
            assert isinstance(trades, list)

    @pytest.mark.asyncio
    async def test_backfill_parallel(self, mock_settings):
        """Test parallel backfill functionality."""
        history = BybitConnectorHistory(mock_settings)
        
        start_dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(2025, 1, 1, 0, 30, 0, tzinfo=timezone.utc)  # 30 minutes
        
        # Mock the paginated fetch to return different trades for different chunks
        def mock_fetch_paginated(chunk_start, chunk_end):
            chunk_time = int(chunk_start.timestamp())
            return [
                history._parse_bybit_trade({
                    "execId": f"trade_{chunk_time}",
                    "price": "50000.0",
                    "size": "0.1",
                    "side": "Buy",
                    "time": str(chunk_time * 1000)
                })
            ]
        
        with patch.object(history, '_fetch_trades_paginated', side_effect=mock_fetch_paginated):
            trades = await history._backfill_parallel(start_dt, end_dt)
            
            # Should have trades from multiple chunks
            assert len(trades) >= 3  # At least 3 chunks for 30 minutes
            # Trades should be sorted by timestamp
            timestamps = [trade.ts for trade in trades]
            assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_error_handling_and_retry(self, mock_settings):
        """Test error handling and retry logic."""
        history = BybitConnectorHistory(mock_settings)
        
        with patch.object(history.http_client, 'fetch_public_trades', new_callable=AsyncMock) as mock_fetch:
            # First attempt fails, second succeeds
            mock_fetch.side_effect = [
                Exception("Network error"),
                [{"execId": "retry_trade", "price": "50000.0", "size": "0.1", "side": "Buy", "time": "1640995200000"}]
            ]
            
            with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
                trades = await history._fetch_trades_paginated(
                    datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
                    datetime(2025, 1, 1, 0, 10, 0, tzinfo=timezone.utc)
                )
                
                # Should have retried and succeeded
                assert len(trades) == 1
                assert trades[0].price == 50000.0
                mock_sleep.assert_called()  # Exponential backoff sleep

    @pytest.mark.asyncio
    async def test_cache_integration(self, mock_settings):
        """Test cache integration when enabled."""
        mock_settings.backfill_cache_enabled = True
        history = BybitConnectorHistory(mock_settings)
        
        # Mock cache manager
        mock_cache_manager = MagicMock()
        mock_cache_manager.load_cached_trades.return_value = None  # Cache miss
        mock_cache_manager.save_trades_to_cache = MagicMock()
        history.cache_manager = mock_cache_manager
        
        start_dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(2025, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        
        with patch.object(history, '_backfill_parallel', new_callable=AsyncMock) as mock_backfill:
            mock_backfill.return_value = []
            
            await history.backfill_with_cache(start_dt, end_dt)
            
            # Should have attempted to load cache and saved result
            mock_cache_manager.load_cached_trades.assert_called_once()
            mock_cache_manager.save_trades_to_cache.assert_called_once()
            mock_backfill.assert_called_once()

    def test_ensure_utc_timezone_handling(self, mock_settings):
        """Test UTC timezone handling."""
        history = BybitConnectorHistory(mock_settings)
        
        # Naive datetime should be converted to UTC
        naive_dt = datetime(2025, 1, 1, 12, 0, 0)
        utc_dt = history._ensure_utc(naive_dt)
        
        assert utc_dt.tzinfo == timezone.utc
        assert utc_dt.hour == 12
        
        # Already UTC datetime should remain unchanged
        utc_dt2 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result_dt = history._ensure_utc(utc_dt2)
        
        assert result_dt == utc_dt2

    @pytest.mark.asyncio
    async def test_rate_limit_adaptation(self, mock_settings):
        """Test dynamic concurrency adaptation under rate limit pressure."""
        history = BybitConnectorHistory(mock_settings)
        
        # Simulate rate limit pressure
        history.http_client.on_rate_limit_error()
        history.http_client.on_rate_limit_error()
        history.http_client.on_rate_limit_error()
        
        # Circuit breaker should be open
        assert history.http_client.circuit_state.value == "open"
        
        # Recommended concurrency should be reduced
        recommended = history.http_client.get_recommended_concurrency(8)
        assert recommended <= 2  # Should be significantly reduced

    @pytest.mark.asyncio
    async def test_bybit_specific_trade_id_handling(self, mock_settings):
        """Test handling of Bybit-specific string trade IDs."""
        history = BybitConnectorHistory(mock_settings)
        
        # Test with string ID that can't be converted to int
        raw_trade = {
            "execId": "very_long_string_trade_id_12345abcdef",
            "symbol": "BTCUSDT",
            "price": "50000.0",
            "size": "0.1",
            "side": "Buy",
            "time": "1640995200000"
        }
        
        trade = history._parse_bybit_trade(raw_trade)
        
        # Should use hash fallback for string IDs
        assert isinstance(trade.id, int)
        assert trade.id > 0

    @pytest.mark.asyncio
    async def test_performance_target_72_chunks(self, mock_settings):
        """Test that 72 chunks can be processed within 15 seconds target."""
        history = BybitConnectorHistory(mock_settings)
        
        # Mock fast responses to simulate optimal conditions
        def mock_fast_fetch(chunk_start, chunk_end):
            return [history._parse_bybit_trade({
                "execId": f"fast_trade_{int(chunk_start.timestamp())}",
                "price": "50000.0",
                "size": "0.1",
                "side": "Buy",
                "time": str(int(chunk_start.timestamp() * 1000))
            })]
        
        start_time = asyncio.get_event_loop().time()
        
        with patch.object(history, '_fetch_trades_paginated', side_effect=mock_fast_fetch):
            # 12 hours = 72 chunks of 10 minutes each
            start_dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
            end_dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            
            trades = await history._backfill_parallel(start_dt, end_dt)
            
            end_time = asyncio.get_event_loop().time()
            elapsed = end_time - start_time
            
            # Should complete quickly with mocked fast responses
            assert elapsed < 5.0  # Much faster than 15s target
            assert len(trades) == 72  # One trade per chunk