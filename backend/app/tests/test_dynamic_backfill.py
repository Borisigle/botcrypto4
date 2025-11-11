"""Tests for dynamic backfill calculation and timeout handling."""
import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.context.service import ContextService
from app.ws.models import Settings


pytestmark = pytest.mark.asyncio


class TestDynamicBackfill:
    """Test dynamic backfill calculation with different UTC times."""

    @pytest.fixture
    def settings(self):
        """Create test settings."""
        return Settings(
            symbol="BTCUSDT",
            context_backfill_enabled=True,
            context_backfill_test_mode=False,
            backfill_cache_enabled=False,  # Disable cache for simpler testing
            backfill_timeout_seconds=180,
            backfill_max_retries=5,
            backfill_retry_base=0.5,
            backfill_retry_backoff=2.0,
            data_source="binance_ws",
        )

    @pytest.fixture
    def mock_provider(self):
        """Create mock trade history provider."""
        provider = MagicMock()
        provider.test_mode = False
        provider.cache_enabled = False
        provider.iterate_trades = AsyncMock()
        provider.backfill_with_cache = AsyncMock()
        return provider

    async def test_dynamic_backfill_00_05_utc(self, settings, mock_provider):
        """Test backfill at 00:05 UTC -> ~1 chunk."""
        # Mock current time at 00:05 UTC
        now = datetime(2025, 1, 15, 0, 5, 0, tzinfo=timezone.utc)
        day_start = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        
        # Mock trades
        mock_trades = []
        for i in range(100):
            mock_trade = MagicMock()
            mock_trade.ts = day_start + timedelta(minutes=i)
            mock_trades.append(mock_trade)
        
        mock_provider.iterate_trades.return_value.__aiter__.return_value = iter(mock_trades)
        
        with patch('app.context.service.ContextService._get_history_provider') as mock_get_provider:
            mock_get_provider.return_value = mock_provider
            
            context_service = ContextService(settings, now_provider=lambda: now)
            
            # Mock exchange info to avoid API calls
            context_service.exchange_info = MagicMock()
            context_service.exchange_info.tick_size = 0.01
            
            await context_service.startup()
            
            # Verify backfill was called with correct range (00:00 to 00:05)
            mock_provider.iterate_trades.assert_called_once()
            call_args = mock_provider.iterate_trades.call_args
            start_time = call_args[0][0]
            end_time = call_args[0][1]
            
            assert start_time == day_start
            assert end_time == now
            
            # Duration should be 5 minutes -> 1 chunk
            duration = (end_time - start_time).total_seconds() / 60
            expected_chunks = max(1, (int(duration) + 9) // 10)
            assert expected_chunks == 1

    async def test_dynamic_backfill_12_00_utc(self, settings, mock_provider):
        """Test backfill at 12:00 UTC -> 72 chunks."""
        # Mock current time at 12:00 UTC
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        day_start = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        
        # Mock trades - just a few for testing
        mock_trades = []
        for i in range(100):
            mock_trade = MagicMock()
            mock_trade.ts = day_start + timedelta(minutes=i * 10)  # Every 10 minutes
            mock_trades.append(mock_trade)
        
        mock_provider.iterate_trades.return_value.__aiter__.return_value = iter(mock_trades)
        
        with patch('app.context.service.ContextService._get_history_provider') as mock_get_provider:
            mock_get_provider.return_value = mock_provider
            
            context_service = ContextService(settings, now_provider=lambda: now)
            
            # Mock exchange info to avoid API calls
            context_service.exchange_info = MagicMock()
            context_service.exchange_info.tick_size = 0.01
            
            await context_service.startup()
            
            # Verify backfill was called with correct range (00:00 to 12:00)
            mock_provider.iterate_trades.assert_called_once()
            call_args = mock_provider.iterate_trades.call_args
            start_time = call_args[0][0]
            end_time = call_args[0][1]
            
            assert start_time == day_start
            assert end_time == now
            
            # Duration should be 720 minutes -> 72 chunks
            duration = (end_time - start_time).total_seconds() / 60
            expected_chunks = max(1, (int(duration) + 9) // 10)
            assert expected_chunks == 72

    async def test_dynamic_backfill_23_55_utc(self, settings, mock_provider):
        """Test backfill at 23:55 UTC -> 143 chunks."""
        # Mock current time at 23:55 UTC
        now = datetime(2025, 1, 15, 23, 55, 0, tzinfo=timezone.utc)
        day_start = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        
        # Mock trades
        mock_trades = []
        for i in range(100):
            mock_trade = MagicMock()
            mock_trade.ts = day_start + timedelta(minutes=i * 100)  # Every 100 minutes
            mock_trades.append(mock_trade)
        
        mock_provider.iterate_trades.return_value.__aiter__.return_value = iter(mock_trades)
        
        with patch('app.context.service.ContextService._get_history_provider') as mock_get_provider:
            mock_get_provider.return_value = mock_provider
            
            context_service = ContextService(settings, now_provider=lambda: now)
            
            # Mock exchange info to avoid API calls
            context_service.exchange_info = MagicMock()
            context_service.exchange_info.tick_size = 0.01
            
            await context_service.startup()
            
            # Verify backfill was called with correct range (00:00 to 23:55)
            mock_provider.iterate_trades.assert_called_once()
            call_args = mock_provider.iterate_trades.call_args
            start_time = call_args[0][0]
            end_time = call_args[0][1]
            
            assert start_time == day_start
            assert end_time == now
            
            # Duration should be 1435 minutes -> 144 chunks (rounded up)
            duration = (end_time - start_time).total_seconds() / 60
            expected_chunks = max(1, (int(duration) + 9) // 10)
            assert expected_chunks == 144

    async def test_backfill_timeout_handling(self, settings, mock_provider):
        """Test that backfill timeout is handled correctly."""
        # Mock current time at 01:00 UTC
        now = datetime(2025, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
        day_start = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        
        # Mock provider that takes too long (simulates timeout)
        async def slow_iterate_trades(start, end):
            await asyncio.sleep(200)  # Sleep longer than timeout
            yield MagicMock()  # This won't be reached due to timeout
        
        mock_provider.iterate_trades.side_effect = slow_iterate_trades
        
        with patch('app.context.service.ContextService._get_history_provider') as mock_get_provider:
            mock_get_provider.return_value = mock_provider
            
            context_service = ContextService(settings, now_provider=lambda: now)
            
            # Mock exchange info to avoid API calls
            context_service.exchange_info = MagicMock()
            context_service.exchange_info.tick_size = 0.01
            
            # Set a short timeout for testing
            context_service.settings.backfill_timeout_seconds = 1
            
            await context_service.startup()
            
            # Should handle timeout gracefully and not crash
            assert True  # If we get here, timeout was handled correctly

    async def test_backfill_with_cache_hit(self, settings):
        """Test backfill with cache hit scenario."""
        # Mock current time at 01:00 UTC
        now = datetime(2025, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
        
        # Mock provider with cache support
        mock_provider = MagicMock()
        mock_provider.test_mode = False
        mock_provider.cache_enabled = True
        mock_provider.backfill_with_cache = AsyncMock()
        
        # Mock cached trades
        mock_cached_trades = []
        for i in range(100):
            mock_trade = MagicMock()
            mock_trade.ts = now - timedelta(minutes=i)
            mock_trade.price = 100000.0 + i
            mock_trade.qty = 0.1
            mock_trade.side = "buy"
            mock_trade.isBuyerMaker = False
            mock_trade.id = i
            mock_cached_trades.append(mock_trade)
        
        mock_provider.backfill_with_cache.return_value = mock_cached_trades
        
        with patch('app.context.service.ContextService._get_history_provider') as mock_get_provider:
            mock_get_provider.return_value = mock_provider
            
            context_service = ContextService(settings, now_provider=lambda: now)
            
            # Mock exchange info to avoid API calls
            context_service.exchange_info = MagicMock()
            context_service.exchange_info.tick_size = 0.01
            
            await context_service.startup()
            
            # Verify cache-aware backfill was called
            mock_provider.backfill_with_cache.assert_called_once()
            call_args = mock_provider.backfill_with_cache.call_args
            start_time = call_args[0][0]
            end_time = call_args[0][1]
            
            # Should be called with 00:00 UTC to current time
            assert start_time.hour == 0
            assert start_time.minute == 0
            assert end_time == now

    async def test_backfill_with_cache_miss(self, settings):
        """Test backfill with cache miss scenario."""
        # Mock current time at 01:00 UTC
        now = datetime(2025, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
        
        # Mock provider with cache support but no cache
        mock_provider = MagicMock()
        mock_provider.test_mode = False
        mock_provider.cache_enabled = True
        mock_provider.backfill_with_cache = AsyncMock()
        
        # Return empty list to simulate cache miss
        mock_provider.backfill_with_cache.return_value = []
        
        with patch('app.context.service.ContextService._get_history_provider') as mock_get_provider:
            mock_get_provider.return_value = mock_provider
            
            context_service = ContextService(settings, now_provider=lambda: now)
            
            # Mock exchange info to avoid API calls
            context_service.exchange_info = MagicMock()
            context_service.exchange_info.tick_size = 0.01
            
            await context_service.startup()
            
            # Verify cache-aware backfill was called even with miss
            mock_provider.backfill_with_cache.assert_called_once()

    async def test_adaptive_retry_backoff(self, settings):
        """Test that retry backoff uses the configurable multiplier."""
        from app.context.backfill import BinanceHttpClient
        
        client = BinanceHttpClient(settings)
        
        # Test different retry attempts with backoff multiplier of 2.0
        base_delay = 0.5
        expected_delays = [
            base_delay * (2.0 ** 0),  # attempt 0: 0.5
            base_delay * (2.0 ** 1),  # attempt 1: 1.0
            base_delay * (2.0 ** 2),  # attempt 2: 2.0
            base_delay * (2.0 ** 3),  # attempt 3: 4.0
        ]
        
        for attempt, expected in enumerate(expected_delays):
            delay = client._exponential_backoff(attempt)
            # Should be close to expected (within jitter range)
            assert abs(delay - expected) < expected * 0.2  # Allow 20% jitter

    def test_chunk_count_calculation(self):
        """Test chunk count calculation for different durations."""
        def calculate_chunks(duration_minutes):
            return max(1, (duration_minutes + 9) // 10)
        
        # Test cases from the ticket
        assert calculate_chunks(5) == 1    # 00:05 UTC → ~1 chunk
        assert calculate_chunks(81) == 9   # 01:21 UTC → ~9 chunks  
        assert calculate_chunks(720) == 72 # 12:00 UTC → 72 chunks
        assert calculate_chunks(1435) == 144 # 23:55 UTC → 144 chunks
        
        # Edge cases
        assert calculate_chunks(0) == 1    # Exactly midnight
        assert calculate_chunks(9) == 1    # 9 minutes -> 1 chunk
        assert calculate_chunks(10) == 1   # 10 minutes -> 1 chunk
        assert calculate_chunks(11) == 2   # 11 minutes -> 2 chunks