"""Historical trade backfill utilities for initializing context state."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator, Callable, Optional, Protocol, List, Tuple

import aiohttp

from app.ws.models import Settings, TradeTick
from app.ws.trades import parse_trade_message

logger = logging.getLogger("context.backfill")


class TradeHistoryProvider(Protocol):
    """Abstract interface for iterating historical trades."""

    async def iterate_trades(self, start: datetime, end: datetime) -> AsyncIterator[TradeTick]:
        """Yield trades ordered by timestamp covering ``start`` to ``end``."""


class BinanceHttpClient:
    """HTTP client for Binance API with proper headers, session management, and retry logic."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
        self.max_retries = settings.backfill_max_retries
        self.retry_base = settings.backfill_retry_base
        
    async def connect(self):
        """Initialize HTTP session if not already created."""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=self.settings.binance_api_timeout)
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
            self.session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=timeout,
                connector=connector
            )
            logger.info("HTTP session created, headers set")
    
    async def close(self):
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def fetch_agg_trades(
        self,
        symbol: str,
        start_time: int,
        end_time: int,
        limit: int = 1000
    ) -> List[dict]:
        """Fetch aggregated trades with retry logic."""
        await self.connect()
        
        url = f"{self.settings.rest_base_url.rstrip('/')}/fapi/v1/aggTrades"
        params = {
            "symbol": symbol.upper(),
            "startTime": start_time,
            "endTime": end_time,
            "limit": limit
        }
        
        for attempt in range(self.max_retries):
            try:
                async with self.session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status in {418, 429, 451}:
                        # Rate limited, blocked, or geographic restriction
                        if attempt < self.max_retries - 1:
                            delay = self._exponential_backoff(attempt)
                            logger.warning(
                                f"HTTP {resp.status} error, retrying in {delay:.2f}s (attempt {attempt+1}/{self.max_retries})"
                            )
                            await asyncio.sleep(delay)
                            continue
                        else:
                            raise Exception(f"Max retries exceeded for {url} (HTTP {resp.status})")
                    else:
                        logger.error(f"Unexpected status {resp.status}")
                        raise Exception(f"HTTP {resp.status}")
            except asyncio.TimeoutError:
                if attempt < self.max_retries - 1:
                    delay = self._exponential_backoff(attempt)
                    logger.warning(f"Timeout on attempt {attempt+1}, retrying in {delay:.2f}s...")
                    await asyncio.sleep(delay)
                    continue
                raise
            except Exception as e:
                if attempt < self.max_retries - 1:
                    delay = self._exponential_backoff(attempt)
                    logger.warning(f"Error on attempt {attempt+1}, retrying in {delay:.2f}s: {e}")
                    await asyncio.sleep(delay)
                    continue
                raise
        
        raise Exception("All retries failed")
    
    def _exponential_backoff(self, attempt: int, base: Optional[float] = None, max_delay: float = 30) -> float:
        """Calculate exponential backoff with jitter."""
        if base is None:
            base = self.retry_base
        delay = base * (2 ** attempt)
        delay = min(delay, max_delay)
        jitter = delay * 0.2 * (random.random() - 0.5)
        return delay + jitter


class BinanceTradeHistory:
    """Paginated loader for Binance aggregated trades using the REST API."""

    def __init__(
        self,
        settings: Settings,
        *,
        limit: int = 1000,
        request_delay: float = 0.1,
        max_retries: int = 5,
        chunk_minutes: int = 10,
        max_concurrent_chunks: int = 10,
        max_iterations_per_chunk: int = 500,
    ) -> None:
        self.settings = settings
        self.limit = max(1, limit)
        self.request_delay = max(0.0, request_delay)
        self._max_retries = max(0, max_retries)
        self.chunk_minutes = max(1, chunk_minutes)
        self.max_concurrent_chunks = max(1, max_concurrent_chunks)
        self.max_iterations_per_chunk = max(1, max_iterations_per_chunk)
        self.http_client = BinanceHttpClient(settings)

    async def iterate_trades(self, start: datetime, end: datetime) -> AsyncIterator[TradeTick]:
        start_utc = self._ensure_utc(start)
        end_utc = self._ensure_utc(end)
        start_ms = int(start_utc.timestamp() * 1000)
        end_ms = int(end_utc.timestamp() * 1000)
        if start_ms > end_ms:
            return

        try:
            # For small windows (< 30 minutes), use single-threaded approach
            if end_ms - start_ms < 30 * 60 * 1000:
                trades = await self._fetch_trades_paginated(start_utc, end_utc)
                for trade in trades:
                    yield trade
                return

            # Use parallel backfill for larger windows
            all_trades = await self._backfill_parallel(start_utc, end_utc)
            
            # Sort by timestamp to ensure chronological order
            all_trades.sort(key=lambda t: t.ts)
            
            # Yield trades one by one
            for trade in all_trades:
                yield trade
        finally:
            await self.http_client.close()

    async def _backfill_parallel(self, start_dt: datetime, end_dt: datetime) -> List[TradeTick]:
        """Split time window into chunks and download in parallel."""
        import time
        start_time = time.time()
        
        chunks = self._split_time_range(start_dt, end_dt, self.chunk_minutes)
        
        logger.info(
            "Backfill parallel: downloading %d chunks (%d min each) from %s to %s",
            len(chunks),
            self.chunk_minutes,
            start_dt.isoformat(),
            end_dt.isoformat(),
        )
        
        semaphore = asyncio.Semaphore(self.max_concurrent_chunks)
        
        async def fetch_chunk(chunk_start: datetime, chunk_end: datetime) -> List[TradeTick]:
            async with semaphore:
                try:
                    return await self._fetch_trades_paginated(chunk_start, chunk_end)
                except Exception as exc:
                    logger.error(
                        "Backfill chunk failed: %s to %s - error=%s",
                        chunk_start.isoformat(),
                        chunk_end.isoformat(),
                        exc,
                    )
                    return []
        
        # Download all chunks in parallel
        tasks = [fetch_chunk(c_start, c_end) for c_start, c_end in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Flatten results and deduplicate by trade ID
        all_trades = []
        seen_ids = set()
        total_chunks_successful = 0
        
        for result in results:
            if isinstance(result, Exception):
                logger.error("Backfill chunk exception: %s", result)
                continue
            
            chunk_trades = result  # type: ignore
            if chunk_trades:
                total_chunks_successful += 1
                
            for trade in chunk_trades:
                if trade.id not in seen_ids:
                    all_trades.append(trade)
                    seen_ids.add(trade.id)
        
        # Safety check: warn if too many trades for the window
        window_hours = (end_dt - start_dt).total_seconds() / 3600
        if len(all_trades) > 500000 and window_hours < 24:
            logger.warning(
                "Backfill sanity check: %d trades for %.1f hour window (expected ~50-150k)",
                len(all_trades),
                window_hours,
            )
        
        elapsed_time = time.time() - start_time
        vwap = (
            sum(trade.price * trade.qty for trade in all_trades) / sum(trade.qty for trade in all_trades)
            if all_trades else 0.0
        )
        logger.info(
            "Backfill complete: %d trades in %.2fs, VWAP=%.3f, %d/%d chunks successful",
            len(all_trades),
            elapsed_time,
            vwap,
            total_chunks_successful,
            len(chunks),
        )
        
        return all_trades

    def _split_time_range(self, start_dt: datetime, end_dt: datetime, chunk_minutes: int) -> List[Tuple[datetime, datetime]]:
        """Split time range into chunks of specified minutes."""
        chunks = []
        current_start = start_dt
        chunk_delta = timedelta(minutes=chunk_minutes)
        
        while current_start < end_dt:
            chunk_end = min(current_start + chunk_delta, end_dt)
            chunks.append((current_start, chunk_end))
            current_start = chunk_end
        
        return chunks

    async def _fetch_trades_paginated(self, start_dt: datetime, end_dt: datetime) -> List[TradeTick]:
        """Fetch trades for a time window with fixed pagination logic."""
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        
        trades = []
        current_start = start_ms
        iteration = 0
        total_retries = 0
        
        while current_start <= end_ms and iteration < self.max_iterations_per_chunk:
            try:
                payload = await self.http_client.fetch_agg_trades(
                    symbol=self.settings.symbol,
                    start_time=current_start,
                    end_time=end_ms,
                    limit=self.limit,
                )
                
                if not isinstance(payload, list) or not payload:
                    break
                
                # Parse trades
                for raw in payload:
                    try:
                        tick = parse_trade_message(raw)
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.debug("backfill_parse_skip error=%s payload=%s", exc, raw)
                        continue
                    
                    # Filter by time window (API might return slightly out-of-bounds trades)
                    if tick.ts < start_dt or tick.ts > end_dt:
                        continue
                    
                    trades.append(tick)
                
                # Check if we got all trades in this batch
                if len(payload) < self.limit:
                    break
                
                # Fixed pagination: use last trade timestamp + 1ms
                raw_last_ts = payload[-1].get("T")
                if raw_last_ts is None:
                    break
                
                last_ts = int(raw_last_ts)
                current_start = last_ts + 1
                
                # Safety check: if we've reached the end of the window
                if current_start >= end_ms:
                    break
                
                iteration += 1
                
                # Progress logging every 10 iterations (not every trade)
                if iteration % 10 == 0:
                    logger.info(
                        "Backfill chunk progress: %d trades loaded, %d iterations, %d retries",
                        len(trades),
                        iteration,
                        total_retries,
                    )
                
                if self.request_delay > 0:
                    await asyncio.sleep(self.request_delay)
                    
            except Exception as e:
                total_retries += 1
                if "Max retries exceeded" in str(e) or total_retries > self._max_retries * 2:
                    logger.error(
                        "Backfill failed for window %s to %s after %d retries: %s",
                        start_dt.isoformat(),
                        end_dt.isoformat(),
                        total_retries,
                        e,
                    )
                    raise
                logger.warning(
                    "Backfill retry %d for window %s to %s: %s",
                    total_retries,
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                    e,
                )
                await asyncio.sleep(self.http_client._exponential_backoff(total_retries))
        
        # Safety limit check
        if iteration >= self.max_iterations_per_chunk - 1:
            logger.error(
                "Backfill safety limit reached for window %s to %s: %d iterations, %d trades",
                start_dt.isoformat(),
                end_dt.isoformat(),
                iteration,
                len(trades),
            )
        
        return trades

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)