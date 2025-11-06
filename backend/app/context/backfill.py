"""Historical trade backfill utilities for initializing context state."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator, Callable, Optional, Protocol, List, Tuple

import httpx

from app.ws.models import Settings, TradeTick
from app.ws.trades import parse_trade_message

logger = logging.getLogger("context.backfill")


class TradeHistoryProvider(Protocol):
    """Abstract interface for iterating historical trades."""

    async def iterate_trades(self, start: datetime, end: datetime) -> AsyncIterator[TradeTick]:
        """Yield trades ordered by timestamp covering ``start`` to ``end``."""


class BinanceTradeHistory:
    """Paginated loader for Binance aggregated trades using the REST API."""

    def __init__(
        self,
        settings: Settings,
        *,
        limit: int = 1000,
        request_delay: float = 0.1,
        http_client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
        max_retries: int = 5,
        chunk_minutes: int = 10,
        max_concurrent_chunks: int = 10,
        max_iterations_per_chunk: int = 500,
    ) -> None:
        self.settings = settings
        self.limit = max(1, limit)
        self.request_delay = max(0.0, request_delay)
        self._http_client_factory = http_client_factory or self._default_client_factory
        self._max_retries = max(0, max_retries)
        self._retry_base_delay = 0.25
        self._retry_max_delay = 5.0
        self.chunk_minutes = max(1, chunk_minutes)
        self.max_concurrent_chunks = max(1, max_concurrent_chunks)
        self.max_iterations_per_chunk = max(1, max_iterations_per_chunk)

    async def iterate_trades(self, start: datetime, end: datetime) -> AsyncIterator[TradeTick]:
        start_utc = self._ensure_utc(start)
        end_utc = self._ensure_utc(end)
        start_ms = int(start_utc.timestamp() * 1000)
        end_ms = int(end_utc.timestamp() * 1000)
        if start_ms > end_ms:
            return

        # For small windows (< 30 minutes), use single-threaded approach
        if end_ms - start_ms < 30 * 60 * 1000:
            async for trade in self._fetch_trades_paginated(start_utc, end_utc):
                yield trade
            return

        # Use parallel backfill for larger windows
        all_trades = await self._backfill_parallel(start_utc, end_utc)
        
        # Sort by timestamp to ensure chronological order
        all_trades.sort(key=lambda t: t.ts)
        
        # Yield trades one by one
        for trade in all_trades:
            yield trade

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
        logger.info(
            "Backfill complete: %d trades in %.2fs, VWAP=%.3f",
            len(all_trades),
            elapsed_time,
            sum(trade.price * trade.qty for trade in all_trades) / sum(trade.qty for trade in all_trades) if all_trades else 0.0,
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
        
        endpoint = f"{self.settings.rest_base_url.rstrip('/')}/fapi/v1/aggTrades"
        params_base = {
            "symbol": self.settings.symbol.upper(),
            "limit": self.limit,
        }
        
        trades = []
        current_start = start_ms
        iteration = 0
        
        async with self._http_client_factory() as client:
            while current_start <= end_ms and iteration < self.max_iterations_per_chunk:
                response = await self._request_with_retry(
                    client,
                    endpoint,
                    params_base,
                    start_time=current_start,
                    end_time=end_ms,
                )
                payload = response.json()
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
                        "Backfill chunk progress: %d trades loaded, %d iterations",
                        len(trades),
                        iteration,
                    )
                
                if self.request_delay > 0:
                    await asyncio.sleep(self.request_delay)
        
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

    def _default_client_factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        params_base: dict[str, int | str],
        *,
        start_time: int,
        end_time: int,
    ) -> httpx.Response:
        attempt = 0
        while True:
            params = dict(params_base)
            params["startTime"] = start_time
            params["endTime"] = end_time
            try:
                response = await client.get(endpoint, params=params)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code in {418, 429, 500, 503}
                    and attempt < self._max_retries
                ):
                    delay = min(self._retry_base_delay * (2**attempt), self._retry_max_delay)
                    logger.warning(
                        "backfill_http_retry status=%s delay=%.2f attempt=%d",
                        exc.response.status_code,
                        delay,
                        attempt + 1,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                raise
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    delay = min(self._retry_base_delay * (2**attempt), self._retry_max_delay)
                    logger.warning(
                        "backfill_transport_retry error=%s delay=%.2f attempt=%d",
                        exc,
                        delay,
                        attempt + 1,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                raise
