"""Backfill cache management for persistent trade history storage."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

import polars as pl

logger = logging.getLogger("context.backfill_cache")


class BackfillCacheManager:
    """Manages persistent Parquet cache for backfilled trades."""
    
    def __init__(self, cache_dir: str):
        """Initialize cache manager.
        
        Args:
            cache_dir: Directory path for storing cache files.
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Backfill cache manager initialized at {self.cache_dir}")
    
    def get_cache_path(self, date: datetime) -> Path:
        """Return cache file path for given date.
        
        Args:
            date: Date to get cache path for.
            
        Returns:
            Path object for the cache file.
        """
        date_str = date.strftime('%Y-%m-%d')
        return self.cache_dir / f"backfill_{date_str}.parquet"
    
    def load_cached_trades(self, date: datetime) -> Optional[List[Dict[str, Any]]]:
        """Load trades from cache if exists.
        
        Args:
            date: Date to load cache for.
            
        Returns:
            List of trade dictionaries if cache exists, None otherwise.
        """
        cache_path = self.get_cache_path(date)
        if not cache_path.exists():
            return None
        
        try:
            df = pl.read_parquet(cache_path)
            trades = df.to_dicts()
            logger.info(f"Loaded {len(trades)} trades from cache: {cache_path.name}")
            return trades
        except Exception as e:
            logger.error(f"Failed to load cache from {cache_path}: {e}")
            return None
    
    def save_trades_to_cache(self, trades: List[Dict[str, Any]], date: datetime) -> None:
        """Save trades to cache in Parquet format.
        
        Args:
            trades: List of trade dictionaries to save.
            date: Date to save cache for.
        """
        if not trades:
            logger.warning(f"No trades to save for {date.strftime('%Y-%m-%d')}")
            return
        
        try:
            df = pl.DataFrame(trades)
            cache_path = self.get_cache_path(date)
            df.write_parquet(cache_path)
            
            # Calculate file size in MB
            file_size_mb = cache_path.stat().st_size / (1024 * 1024)
            logger.info(
                f"Saved {len(trades)} trades to {cache_path.name} ({file_size_mb:.1f} MB)"
            )
        except Exception as e:
            logger.error(f"Failed to save cache for {date.strftime('%Y-%m-%d')}: {e}")
    
    def get_last_cached_timestamp(self, trades: List[Dict[str, Any]]) -> Optional[int]:
        """Return timestamp of last trade in cache (in milliseconds).
        
        Args:
            trades: List of trade dictionaries.
            
        Returns:
            Timestamp in milliseconds, or None if trades is empty.
        """
        if not trades:
            return None
        
        # Find the field name for timestamp (could be 'T', 'timestamp', 'ts', etc.)
        last_trade = trades[-1]
        
        # Try common field names
        for field in ['T', 'timestamp', 'ts', 'time']:
            if field in last_trade:
                return last_trade[field]
        
        logger.warning(f"Could not find timestamp field in trade: {last_trade}")
        return None
    
    def deduplicate_trades(self, trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate trades by trade ID, preserving order.
        
        Args:
            trades: List of trade dictionaries (may contain duplicates).
            
        Returns:
            Deduplicated list of trades sorted by timestamp, with duplicates removed.
        """
        if not trades:
            return []
        
        # Find the trade ID field (could be 'a', 'id', 'tradeId', etc.)
        trade_id_field = None
        for field in ['a', 'id', 'tradeId', 'aggTradeId']:
            if field in trades[0]:
                trade_id_field = field
                break
        
        if not trade_id_field:
            logger.warning("Could not find trade ID field, returning as-is")
            return trades
        
        # Find timestamp field
        timestamp_field = None
        for field in ['T', 'timestamp', 'ts', 'time']:
            if field in trades[0]:
                timestamp_field = field
                break
        
        if not timestamp_field:
            logger.warning("Could not find timestamp field, deduplicating without sort")
            seen = set()
            deduped = []
            for trade in trades:
                trade_id = trade.get(trade_id_field)
                if trade_id not in seen:
                    seen.add(trade_id)
                    deduped.append(trade)
            return deduped
        
        # Sort by timestamp and deduplicate
        seen = set()
        deduped = []
        for trade in sorted(trades, key=lambda t: t[timestamp_field]):
            trade_id = trade.get(trade_id_field)
            if trade_id not in seen:
                seen.add(trade_id)
                deduped.append(trade)
        
        return deduped
    
    def cleanup_old_cache(self, keep_days: int = 5) -> None:
        """Delete cache files older than keep_days.
        
        Args:
            keep_days: Number of recent days to keep cache for. Default: 5.
        """
        from datetime import timedelta
        
        if not self.cache_dir.exists():
            return
        
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=keep_days)
        deleted_count = 0
        
        for cache_file in self.cache_dir.glob("backfill_*.parquet"):
            try:
                # Extract date from filename
                date_str = cache_file.stem.replace("backfill_", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                
                if file_date < cutoff:
                    cache_file.unlink()
                    logger.info(f"Cleaned up old cache: {cache_file.name}")
                    deleted_count += 1
            except Exception as e:
                logger.warning(f"Failed to cleanup {cache_file.name}: {e}")
        
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old cache files (kept last {keep_days} days)")
