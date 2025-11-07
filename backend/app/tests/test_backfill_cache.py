"""Tests for backfill cache functionality."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any
import pytest
import polars as pl

from app.context.backfill_cache import BackfillCacheManager
from app.context.backfill import BinanceTradeHistory
from app.ws.models import Settings, TradeTick, TradeSide


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def cache_manager(temp_cache_dir):
    """Create a cache manager with temporary directory."""
    return BackfillCacheManager(temp_cache_dir)


@pytest.fixture
def mock_settings():
    """Create mock settings with cache enabled."""
    return Settings(
        backfill_cache_enabled=True,
        backfill_cache_dir="./test_cache",
        context_backfill_enabled=True,
    )


@pytest.fixture
def sample_trades():
    """Create sample trade data for testing."""
    base_time = datetime(2025, 11, 7, 0, 0, 0, tzinfo=timezone.utc)
    trades = []
    for i in range(100):
        ts = base_time + timedelta(minutes=i)
        trades.append(
            TradeTick(
                ts=ts,
                price=100.0 + i * 0.1,
                qty=1.5 + i * 0.01,
                side=TradeSide.BUY if i % 2 == 0 else TradeSide.SELL,
                isBuyerMaker=i % 2 == 0,
                id=1000 + i,
            )
        )
    return trades


class TestBackfillCacheManager:
    """Test BackfillCacheManager class."""

    def test_cache_manager_initialization(self, temp_cache_dir):
        """Test cache manager initializes correctly."""
        manager = BackfillCacheManager(temp_cache_dir)
        
        assert manager.cache_dir.exists()
        assert manager.cache_dir == Path(temp_cache_dir)

    def test_get_cache_path(self, cache_manager):
        """Test cache path generation."""
        date = datetime(2025, 11, 7, tzinfo=timezone.utc)
        path = cache_manager.get_cache_path(date)
        
        assert path.name == "backfill_2025-11-07.parquet"
        assert str(path).endswith("backfill_2025-11-07.parquet")

    def test_save_and_load_trades(self, cache_manager):
        """Test saving and loading trades from cache."""
        date = datetime(2025, 11, 7, tzinfo=timezone.utc)
        trades = [
            {
                "T": 1699296000000,  # 2025-11-07 00:00:00 UTC in ms
                "a": 1000,  # aggTradeId
                "p": 100.5,  # price
                "q": 1.5,  # qty
                "f": 0,  # firstTradeId
                "l": 0,  # lastTradeId
                "m": False,  # isBuyerMaker
                "M": True,
            },
            {
                "T": 1699296060000,  # 1 minute later
                "a": 1001,
                "p": 100.6,
                "q": 1.6,
                "f": 0,
                "l": 0,
                "m": True,
                "M": True,
            },
        ]
        
        # Save trades
        cache_manager.save_trades_to_cache(trades, date)
        
        # Verify file exists
        cache_path = cache_manager.get_cache_path(date)
        assert cache_path.exists()
        
        # Load trades back
        loaded = cache_manager.load_cached_trades(date)
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["a"] == 1000
        assert loaded[1]["a"] == 1001

    def test_load_nonexistent_cache(self, cache_manager):
        """Test loading from non-existent cache returns None."""
        date = datetime(2025, 11, 8, tzinfo=timezone.utc)
        result = cache_manager.load_cached_trades(date)
        
        assert result is None

    def test_get_last_cached_timestamp(self, cache_manager):
        """Test extracting last cached timestamp."""
        trades = [
            {"T": 1699296000000, "a": 1000},
            {"T": 1699296060000, "a": 1001},
            {"T": 1699296120000, "a": 1002},
        ]
        
        last_ts = cache_manager.get_last_cached_timestamp(trades)
        assert last_ts == 1699296120000

    def test_get_last_cached_timestamp_empty(self, cache_manager):
        """Test last timestamp with empty trades."""
        result = cache_manager.get_last_cached_timestamp([])
        assert result is None

    def test_deduplicate_trades_by_trade_id(self, cache_manager):
        """Test deduplication by trade ID."""
        trades = [
            {"T": 1699296000000, "a": 1000, "p": 100.0, "q": 1.0},
            {"T": 1699296060000, "a": 1001, "p": 100.1, "q": 1.0},
            {"T": 1699296120000, "a": 1000, "p": 100.0, "q": 1.0},  # Duplicate
            {"T": 1699296180000, "a": 1002, "p": 100.2, "q": 1.0},
        ]
        
        deduped = cache_manager.deduplicate_trades(trades)
        
        assert len(deduped) == 3  # One duplicate removed
        
        # Check unique trade IDs
        trade_ids = [t["a"] for t in deduped]
        assert trade_ids.count(1000) == 1
        assert trade_ids.count(1001) == 1
        assert trade_ids.count(1002) == 1

    def test_deduplicate_preserves_chronological_order(self, cache_manager):
        """Test that deduplication preserves chronological order."""
        trades = [
            {"T": 1699296180000, "a": 1000, "p": 100.0, "q": 1.0},  # Out of order
            {"T": 1699296000000, "a": 1001, "p": 100.1, "q": 1.0},
            {"T": 1699296060000, "a": 1002, "p": 100.2, "q": 1.0},
        ]
        
        deduped = cache_manager.deduplicate_trades(trades)
        
        assert len(deduped) == 3
        assert deduped[0]["T"] == 1699296000000
        assert deduped[1]["T"] == 1699296060000
        assert deduped[2]["T"] == 1699296180000

    def test_cleanup_old_cache(self, cache_manager):
        """Test cleanup of old cache files."""
        from datetime import timedelta
        
        # Create cache files for different dates
        today = datetime.now(timezone.utc)
        
        for days_ago in [1, 3, 6, 8]:
            date = today - timedelta(days=days_ago)
            trades = [
                {"T": int(date.timestamp() * 1000), "a": 1000, "p": 100.0, "q": 1.0}
            ]
            cache_manager.save_trades_to_cache(trades, date)
        
        # Should have 4 files
        files_before = list(cache_manager.cache_dir.glob("backfill_*.parquet"))
        assert len(files_before) == 4
        
        # Cleanup (keep last 5 days) - removes files older than 5 days
        cache_manager.cleanup_old_cache(keep_days=5)
        
        # Should have 2 files (8 and 6 days ago are removed; 1 and 3 days kept)
        files_after = list(cache_manager.cache_dir.glob("backfill_*.parquet"))
        assert len(files_after) == 2  # 8-day and 6-day-old files removed

    def test_save_empty_trades(self, cache_manager):
        """Test that saving empty trades logs warning."""
        date = datetime(2025, 11, 7, tzinfo=timezone.utc)
        cache_manager.save_trades_to_cache([], date)
        
        # Should not create a file
        cache_path = cache_manager.get_cache_path(date)
        assert not cache_path.exists()

    def test_cache_integrity_parquet_format(self, cache_manager):
        """Test that cache is properly stored in Parquet format."""
        date = datetime(2025, 11, 7, tzinfo=timezone.utc)
        trades = [
            {"T": 1699296000000, "a": 1000, "p": 100.5, "q": 1.5, "m": False},
            {"T": 1699296060000, "a": 1001, "p": 100.6, "q": 1.6, "m": True},
        ]
        
        cache_manager.save_trades_to_cache(trades, date)
        
        # Directly read Parquet to verify format
        cache_path = cache_manager.get_cache_path(date)
        df = pl.read_parquet(cache_path)
        
        assert len(df) == 2
        assert "T" in df.columns
        assert "a" in df.columns
        assert "p" in df.columns
        assert "q" in df.columns


class TestBinanceTradeHistoryWithCache:
    """Test BinanceTradeHistory with cache integration."""

    @pytest.mark.asyncio
    async def test_trade_tick_to_dict_conversion(self, mock_settings, sample_trades):
        """Test conversion of TradeTick to dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                backfill_cache_enabled=True,
                backfill_cache_dir=tmpdir,
                context_backfill_enabled=True,
            )
            history = BinanceTradeHistory(settings)
            
            trade = sample_trades[0]
            trade_dict = history._trade_tick_to_dict(trade)
            
            assert "T" in trade_dict
            assert "a" in trade_dict
            assert "p" in trade_dict
            assert "q" in trade_dict
            assert "m" in trade_dict
            assert trade_dict["a"] == trade.id
            assert trade_dict["p"] == trade.price
            assert trade_dict["q"] == trade.qty

    @pytest.mark.asyncio
    async def test_dicts_to_trade_ticks_conversion(self, mock_settings):
        """Test conversion of dicts to TradeTick objects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                backfill_cache_enabled=True,
                backfill_cache_dir=tmpdir,
                context_backfill_enabled=True,
            )
            history = BinanceTradeHistory(settings)
            
            dicts = [
                {
                    "T": 1699296000000,
                    "a": 1000,
                    "p": 100.5,
                    "q": 1.5,
                    "m": False,
                },
                {
                    "T": 1699296060000,
                    "a": 1001,
                    "p": 100.6,
                    "q": 1.6,
                    "m": True,
                },
            ]
            
            trades = history._dicts_to_trade_ticks(dicts)
            
            assert len(trades) == 2
            assert trades[0].id == 1000
            assert trades[0].price == 100.5
            assert trades[1].isBuyerMaker is True

    @pytest.mark.asyncio
    async def test_roundtrip_conversion(self, mock_settings, sample_trades):
        """Test that trades can be converted to dict and back without loss."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                backfill_cache_enabled=True,
                backfill_cache_dir=tmpdir,
                context_backfill_enabled=True,
            )
            history = BinanceTradeHistory(settings)
            
            # Convert to dict and back
            dicts = [history._trade_tick_to_dict(t) for t in sample_trades[:5]]
            trades_back = history._dicts_to_trade_ticks(dicts)
            
            # Check that essential data is preserved
            for orig, recovered in zip(sample_trades[:5], trades_back):
                assert orig.id == recovered.id
                assert orig.price == recovered.price
                assert orig.qty == recovered.qty
                assert orig.isBuyerMaker == recovered.isBuyerMaker


class TestBackfillCacheIntegration:
    """Integration tests for backfill with cache."""

    @pytest.mark.asyncio
    async def test_cache_manager_creation_with_settings(self, mock_settings):
        """Test that cache manager is created when enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                backfill_cache_enabled=True,
                backfill_cache_dir=tmpdir,
            )
            history = BinanceTradeHistory(settings)
            
            assert history.cache_enabled is True
            assert history.cache_manager is not None
            assert history.cache_manager.cache_dir == Path(tmpdir)

    @pytest.mark.asyncio
    async def test_cache_disabled_by_setting(self, mock_settings):
        """Test that cache manager is not created when disabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                backfill_cache_enabled=False,
                backfill_cache_dir=tmpdir,
            )
            history = BinanceTradeHistory(settings)
            
            assert history.cache_enabled is False
            assert history.cache_manager is None

    def test_cache_file_naming_convention(self, cache_manager):
        """Test that cache files follow naming convention."""
        dates = [
            datetime(2025, 11, 7, tzinfo=timezone.utc),
            datetime(2025, 11, 6, tzinfo=timezone.utc),
            datetime(2025, 1, 1, tzinfo=timezone.utc),
        ]
        
        for date in dates:
            path = cache_manager.get_cache_path(date)
            expected = f"backfill_{date.strftime('%Y-%m-%d')}.parquet"
            assert path.name == expected

    def test_cache_directory_persistence(self, temp_cache_dir):
        """Test that cache persists across manager instances."""
        date = datetime(2025, 11, 7, tzinfo=timezone.utc)
        trades = [
            {"T": 1699296000000, "a": 1000, "p": 100.0, "q": 1.0}
        ]
        
        # Save with first manager
        manager1 = BackfillCacheManager(temp_cache_dir)
        manager1.save_trades_to_cache(trades, date)
        
        # Load with second manager
        manager2 = BackfillCacheManager(temp_cache_dir)
        loaded = manager2.load_cached_trades(date)
        
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["a"] == 1000
