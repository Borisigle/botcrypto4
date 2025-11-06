"""Historical trade backfill utilities for initializing context state."""
from __future__ import annotations

import asyncio
import logging
import random
import time
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator, Callable, Optional, Protocol, List, Tuple
from urllib.parse import urlencode

import aiohttp

from app.ws.models import Settings, TradeTick
from app.ws.trades import parse_trade_message

logger = logging.getLogger("context.backfill")


class TradeHistoryProvider(Protocol):
    """Abstract interface for iterating historical trades."""

    async def iterate_trades(self, start: datetime, end: datetime) -> AsyncIterator[TradeTick]:
        """Yield trades ordered by timestamp covering ``start`` to ``end``."""


class BinanceHttpClient:
    """HTTP client for Binance API with proper headers, session management, retry logic, and optional HMAC authentication."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session: Optional[aiohttp.ClientSession] = None
        self.api_key = settings.binance_api_key
        self.api_secret = settings.binance_api_secret
        self.use_auth = bool(self.api_key and self.api_secret)
        
        # Base headers
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
        
        # Add API key header if authentication is enabled
        if self.use_auth:
            self.headers["X-MBX-APIKEY"] = self.api_key
            # Log truncated API key for security
            key_preview = f"{self.api_key[:4]}...{self.api_key[-4:]}" if len(self.api_key) > 8 else "***"
            logger.info(f"Binance auth: enabled (API key: {key_preview})")
        else:
            logger.info("Binance auth: disabled (using public endpoints)")
            
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
    
    def _sign_request(self, params: dict) -> str:
        """Generate HMAC-SHA256 signature for authenticated requests."""
        if not self.use_auth:
            return ""
            
        # Create query string from parameters
        query_string = urlencode(sorted(params.items()))
        
        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            self.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return signature
    
    async def fetch_agg_trades(
        self,
        symbol: str,
        start_time: int,
        end_time: int,
        limit: int = 1000
    ) -> List[dict]:
        """Fetch aggregated trades with retry logic and optional HMAC authentication."""
        await self.connect()
        
        url = f"{self.settings.rest_base_url.rstrip('/')}/fapi/v1/aggTrades"
        params = {
            "symbol": symbol.upper(),
            "startTime": start_time,
            "endTime": end_time,
            "limit": limit
        }
        
        # Add authentication parameters if enabled
        if self.use_auth:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            signature = self._sign_request(params)
            params["signature"] = signature
        
        for attempt in range(self.max_retries):
            try:
                async with self.session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 401:
                        # Unauthorized - likely bad credentials
                        logger.error("Binance API authentication failed (401). Check your API credentials.")
                        if attempt == 0:
                            logger.info("Falling back to public endpoints for subsequent requests...")
                            self.use_auth = False
                            # Remove auth params and retry
                            params.pop("timestamp", None)
                            params.pop("recvWindow", None)
                            params.pop("signature", None)
                            continue
                        else:
                            raise Exception("Authentication failed and fallback unsuccessful")
                    elif resp.status == 403:
                        # Forbidden - API key restrictions
                        logger.warning("Binance API access forbidden (403). Check API key permissions.")
                        if attempt < self.max_retries - 1:
                            delay = self._exponential_backoff(attempt)
                            logger.warning(f"Retrying in {delay:.2f}s (attempt {attempt+1}/{self.max_retries})")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            raise Exception(f"API access forbidden (403) after {self.max_retries} retries")
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
        max_concurrent_chunks: int = 5,
        max_iterations_per_chunk: int = 500,
    ) -> None:
        self.settings = settings
        self.limit = max(1, limit)
        self.request_delay = max(0.0, request_delay)
        self._max_retries = max(0, max_retries)
        self.chunk_minutes = max(1, chunk_minutes)
        
        # Use higher concurrency when authentication is enabled
        api_key = settings.binance_api_key
        api_secret = settings.binance_api_secret
        use_auth = bool(api_key and api_secret)
        
        if use_auth:
            # Aggressive parallelization with auth (higher rate limits)
            self.max_concurrent_chunks = 20
            self.request_delay = 0.0  # No delay needed with auth
            logger.info("Backfill: Using authenticated mode with 20 concurrent chunks")
        else:
            # Conservative settings for public endpoints
            self.max_concurrent_chunks = max(1, max_concurrent_chunks)
            logger.info(f"Backfill: Using public mode with {self.max_concurrent_chunks} concurrent chunks")
            
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
        """Split time window into chunks and download in parallel with throttling."""
        import time
        start_time = time.time()
        
        chunks = self._split_time_range(start_dt, end_dt, self.chunk_minutes)
        
        logger.info(
            "Backfill: %d chunks (%d min each), max %d concurrent from %s to %s",
            len(chunks),
            self.chunk_minutes,
            self.max_concurrent_chunks,
            start_dt.isoformat(),
            end_dt.isoformat(),
        )
        
        semaphore = asyncio.Semaphore(self.max_concurrent_chunks)
        
        async def fetch_chunk_throttled(chunk_index: int, chunk_start: datetime, chunk_end: datetime) -> Tuple[int, List[TradeTick]]:
            async with semaphore:
                # Add delay only for public endpoints to avoid 418 errors
                if self.request_delay > 0:
                    await asyncio.sleep(self.request_delay + (random.random() * 0.05))
                try:
                    trades = await self._fetch_trades_paginated(chunk_start, chunk_end)
                    return chunk_index, trades
                except Exception as exc:
                    logger.warning(
                        "Chunk %d failed: %s to %s - %s, continuing...",
                        chunk_index,
                        chunk_start.isoformat(),
                        chunk_end.isoformat(),
                        exc,
                    )
                    return chunk_index, []
        
        # Download all chunks in parallel with throttling
        tasks = [fetch_chunk_throttled(i, c_start, c_end) for i, (c_start, c_end) in enumerate(chunks)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Flatten results and deduplicate by trade ID
        all_trades = []
        seen_ids = set()
        successful_chunks = 0
        failed_chunks = 0
        
        for result in results:
            if isinstance(result, Exception):
                failed_chunks += 1
                logger.error("Backfill chunk exception: %s", result)
                continue
            
            chunk_index, chunk_trades = result
            if chunk_trades:
                successful_chunks += 1
            else:
                failed_chunks += 1
                
            for trade in chunk_trades:
                if trade.id not in seen_ids:
                    all_trades.append(trade)
                    seen_ids.add(trade.id)
            
            # Progress logging every 10 chunks
            if (chunk_index + 1) % 10 == 0:
                elapsed = time.time() - start_time
                chunks_per_second = (chunk_index + 1) / elapsed
                remaining_chunks = len(chunks) - (chunk_index + 1)
                eta_seconds = remaining_chunks / chunks_per_second if chunks_per_second > 0 else 0
                logger.info(
                    "Progress: %d/%d chunks processed, ~%d trades, ~%.0fs remaining",
                    chunk_index + 1,
                    len(chunks),
                    len(all_trades),
                    eta_seconds,
                )
        
        # Sort by timestamp to ensure chronological order
        all_trades.sort(key=lambda t: t.ts)
        
        # Safety check: warn if too many trades for the window
        window_hours = (end_dt - start_dt).total_seconds() / 3600
        if len(all_trades) > 500000 and window_hours < 24:
            logger.warning(
                "Backfill sanity check: %d trades for %.1f hour window (expected ~50-150k)",
                len(all_trades),
                window_hours,
            )
        
        elapsed_time = time.time() - start_time
        success_rate = (len(chunks) - failed_chunks) / len(chunks) * 100
        
        # Calculate VWAP
        vwap = (
            sum(trade.price * trade.qty for trade in all_trades) / sum(trade.qty for trade in all_trades)
            if all_trades else 0.0
        )
        
        # Calculate POC (Point of Control - price level with maximum volume)
        poc_price = 0.0
        if all_trades:
            # Group trades by price level (rounded to 3 decimal places for BTCUSDT)
            price_volumes = {}
            for trade in all_trades:
                price_rounded = round(trade.price, 3)
                price_volumes[price_rounded] = price_volumes.get(price_rounded, 0) + trade.qty
            
            # Find price with maximum volume
            if price_volumes:
                poc_price = max(price_volumes, key=price_volumes.get)
        
        logger.info(
            "Backfill complete: ~%d trades in %.1fs, %.1f%% chunks successful (%d/%d)",
            len(all_trades),
            elapsed_time,
            success_rate,
            successful_chunks,
            len(chunks),
        )
        logger.info(
            "  VWAP: %.3f",
            vwap,
        )
        if poc_price > 0:
            logger.info(
                "  POCd: %.3f",
                poc_price,
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