"""Historical trade backfill utilities for initializing context state."""
from __future__ import annotations

import asyncio
import logging
import random
import time
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import AsyncIterator, Callable, Optional, Protocol, List, Tuple, Dict, Any
from urllib.parse import urlencode
from pathlib import Path

import aiohttp

from app.ws.models import Settings, TradeTick
from app.ws.trades import parse_trade_message
from .price_bins import quantize_price_to_tick, get_effective_tick_size, validate_tick_size, PriceBinningError
from .backfill_cache import BackfillCacheManager

logger = logging.getLogger("context.backfill")


class CircuitBreakerState(Enum):
    """Circuit breaker state for rate limit management."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Rate limited, enforcing cooldown
    HALF_OPEN = "half_open"  # Testing recovery


class TradeHistoryProvider(Protocol):
    """Abstract interface for iterating historical trades."""

    async def iterate_trades(self, start: datetime, end: datetime) -> AsyncIterator[TradeTick]:
        """Yield trades ordered by timestamp covering ``start`` to ``end``."""


class BybitHttpClient:
    """HTTP client for Bybit API with proper headers, session management, retry logic, optional authentication, and circuit breaker."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session: Optional[aiohttp.ClientSession] = None
        self.api_key = settings.bybit_api_key
        self.api_secret = settings.bybit_api_secret
        self.use_auth = bool(self.api_key and self.api_secret)
        
        # Base headers for Bybit API
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Content-Type": "application/json"
        }
        
        # Add API key header if authentication is enabled
        if self.use_auth:
            self.headers["X-BAPI-API-KEY"] = self.api_key
            # Log truncated API key for security
            key_preview = f"{self.api_key[:4]}...{self.api_key[-4:]}" if len(self.api_key) > 8 else "***"
            logger.info(f"Bybit auth: enabled (API key: {key_preview})")
        else:
            logger.info("Bybit auth: disabled (using public endpoints)")
            
        self.max_retries = settings.bybit_backfill_max_retries
        self.retry_base = settings.bybit_backfill_retry_base
        
        # Circuit breaker state for rate limit management
        self.circuit_state = CircuitBreakerState.CLOSED
        self.consecutive_rate_limit_errors = 0
        self.rate_limit_threshold = settings.bybit_backfill_rate_limit_threshold
        self.cooldown_seconds = settings.bybit_backfill_cooldown_seconds
        self.cooldown_until: Optional[float] = None
        
        # Rate limit pressure indicators
        self.throttle_multiplier = 1.0  # Start at normal speed
        self.last_rate_limit_ts: Optional[float] = None
        
    async def connect(self):
        """Initialize HTTP session if not already created."""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=self.settings.bybit_api_timeout)
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
            self.session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=timeout,
                connector=connector
            )
            logger.info("Bybit HTTP session created, headers set")
    
    async def close(self):
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None
    
    def _sign_request(self, params: dict, recv_window: int = 5000) -> Tuple[str, str, int]:
        """Generate HMAC-SHA256 signature for authenticated requests."""
        if not self.use_auth:
            return "", "", 0
            
        # Create query string from parameters for signature
        sorted_params = sorted(params.items())
        query_string = urlencode(sorted_params)
        
        # Generate signature: HMAC-SHA256(timestamp + recv_window + queryString)
        timestamp = str(int(time.time() * 1000))
        sign_str = timestamp + str(recv_window) + query_string
        
        signature = hmac.new(
            self.api_secret.encode(),
            sign_str.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return signature, timestamp, recv_window
    
    async def check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is open and enforce cooldown if needed. Returns True if breaker is open."""
        if self.circuit_state != CircuitBreakerState.OPEN:
            return False
        
        if self.cooldown_until is None:
            return False
        
        current_time = time.time()
        if current_time < self.cooldown_until:
            remaining = self.cooldown_until - current_time
            logger.warning(f"Bybit circuit breaker open: cooldown active for {remaining:.1f}s more")
            await asyncio.sleep(min(remaining, 1.0))  # Sleep up to 1 second
            return True
        
        # Cooldown expired, attempt recovery
        self.circuit_state = CircuitBreakerState.HALF_OPEN
        self.consecutive_rate_limit_errors = 0
        logger.info("Bybit circuit breaker: entering HALF_OPEN state to test recovery")
        return False
    
    def on_rate_limit_error(self) -> None:
        """Handle a rate limit error (429/10001/etc)."""
        self.consecutive_rate_limit_errors += 1
        self.last_rate_limit_ts = time.time()
        
        # Increase throttle multiplier (will slow down requests)
        self.throttle_multiplier = min(5.0, self.throttle_multiplier * 1.5)
        
        if self.consecutive_rate_limit_errors >= self.rate_limit_threshold:
            if self.circuit_state != CircuitBreakerState.OPEN:
                self.circuit_state = CircuitBreakerState.OPEN
                self.cooldown_until = time.time() + self.cooldown_seconds
                logger.error(
                    f"Bybit circuit breaker opened: {self.consecutive_rate_limit_errors} consecutive rate limit errors. "
                    f"Enforcing {self.cooldown_seconds}s cooldown (throttle_multiplier={self.throttle_multiplier:.1f}x)"
                )
    
    def on_successful_request(self) -> None:
        """Handle a successful request."""
        self.consecutive_rate_limit_errors = 0
        
        # Gradually recover throttle multiplier (progressive recovery)
        if self.throttle_multiplier > 1.0:
            self.throttle_multiplier = max(1.0, self.throttle_multiplier * 0.95)
        
        if self.circuit_state == CircuitBreakerState.HALF_OPEN:
            self.circuit_state = CircuitBreakerState.CLOSED
            logger.info("Bybit circuit breaker: recovery successful, returning to CLOSED state")
    
    async def fetch_public_trades(
        self,
        symbol: str,
        start_time: int,
        end_time: int,
        limit: int = 1000
    ) -> List[dict]:
        """Fetch public trades with retry logic and circuit breaker."""
        await self.connect()
        
        # Check circuit breaker state
        await self.check_circuit_breaker()
        
        # Bybit public trades endpoint (v5)
        url = f"{self.settings.bybit_rest_base_url.rstrip('/')}/v5/market/recent-trade"
        params = {
            "category": "linear",  # For USDT perpetuals
            "symbol": symbol,
            "limit": min(limit, 1000),
            "start": start_time,  # Start time in milliseconds
            "end": end_time,      # End time in milliseconds
        }
        
        rate_limit_error_count = 0
        for attempt in range(self.max_retries):
            try:
                async with self.session.get(url, params=params) as resp:
                    if resp.status == 200:
                        response_data = await resp.json()
                        self.on_successful_request()
                        
                        # Bybit v5 API response format: {"retCode": 0, "retMsg": "OK", "result": {"list": [...]}, "retExtInfo": {}}
                        if response_data.get("retCode") == 0:
                            return response_data.get("result", {}).get("list", [])
                        else:
                            raise Exception(f"Bybit API error: {response_data.get('retMsg', 'Unknown error')}")
                    
                    elif resp.status in {429, 10001}:
                        # Rate limited
                        rate_limit_error_count += 1
                        self.on_rate_limit_error()
                        
                        if attempt < self.max_retries - 1:
                            delay = self._exponential_backoff(attempt)
                            adjusted_delay = delay * self.throttle_multiplier
                            logger.warning(
                                f"Bybit HTTP {resp.status} error (attempt {attempt+1}/{self.max_retries}), "
                                f"retrying in {adjusted_delay:.2f}s (throttle: {self.throttle_multiplier:.1f}x)"
                            )
                            await asyncio.sleep(adjusted_delay)
                            continue
                        else:
                            raise Exception(f"Max retries exceeded for {url} (HTTP {resp.status}, {rate_limit_error_count} rate limit errors)")
                    
                    else:
                        logger.error(f"Unexpected Bybit status {resp.status}")
                        try:
                            error_text = await resp.text()
                            logger.error(f"Response body: {error_text}")
                        except:
                            pass
                        raise Exception(f"HTTP {resp.status}")
                        
            except asyncio.TimeoutError:
                if attempt < self.max_retries - 1:
                    delay = self._exponential_backoff(attempt)
                    logger.warning(f"Bybit timeout on attempt {attempt+1}, retrying in {delay:.2f}s...")
                    await asyncio.sleep(delay)
                    continue
                raise
            except Exception as e:
                if attempt < self.max_retries - 1:
                    delay = self._exponential_backoff(attempt)
                    logger.warning(f"Bybit error on attempt {attempt+1}, retrying in {delay:.2f}s: {e}")
                    await asyncio.sleep(delay)
                    continue
                raise
        
        raise Exception("All retries failed")
    
    async def fetch_private_trades(
        self,
        symbol: str,
        start_time: int,
        end_time: int,
        limit: int = 1000
    ) -> List[dict]:
        """Fetch private trades with authentication and retry logic."""
        if not self.use_auth:
            raise Exception("Authentication required for private trades")
        
        await self.connect()
        await self.check_circuit_breaker()
        
        # Bybit private trades endpoint (v5)
        url = f"{self.settings.bybit_rest_base_url.rstrip('/')}/v5/execution/list"
        params = {
            "category": "linear",
            "symbol": symbol,
            "limit": min(limit, 1000),
            "startTime": start_time,
            "endTime": end_time,
        }
        
        # Add authentication
        signature, timestamp, recv_window = self._sign_request(params)
        auth_headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(recv_window),
        }
        
        rate_limit_error_count = 0
        for attempt in range(self.max_retries):
            try:
                async with self.session.get(url, params=params, headers=auth_headers) as resp:
                    if resp.status == 200:
                        response_data = await resp.json()
                        self.on_successful_request()
                        
                        if response_data.get("retCode") == 0:
                            return response_data.get("result", {}).get("list", [])
                        else:
                            raise Exception(f"Bybit API error: {response_data.get('retMsg', 'Unknown error')}")
                    
                    elif resp.status in {401, 403}:
                        # Authentication error - switch to public mode
                        logger.warning("Bybit API authentication failed, switching to public endpoints")
                        self.use_auth = False
                        return await self.fetch_public_trades(symbol, start_time, end_time, limit)
                    
                    elif resp.status in {429, 10001}:
                        # Rate limited
                        rate_limit_error_count += 1
                        self.on_rate_limit_error()
                        
                        if attempt < self.max_retries - 1:
                            delay = self._exponential_backoff(attempt)
                            adjusted_delay = delay * self.throttle_multiplier
                            logger.warning(
                                f"Bybit HTTP {resp.status} error (attempt {attempt+1}/{self.max_retries}), "
                                f"retrying in {adjusted_delay:.2f}s (throttle: {self.throttle_multiplier:.1f}x)"
                            )
                            await asyncio.sleep(adjusted_delay)
                            continue
                        else:
                            raise Exception(f"Max retries exceeded for {url} (HTTP {resp.status}, {rate_limit_error_count} rate limit errors)")
                    
                    else:
                        logger.error(f"Unexpected Bybit status {resp.status}")
                        try:
                            error_text = await resp.text()
                            logger.error(f"Response body: {error_text}")
                        except:
                            pass
                        raise Exception(f"HTTP {resp.status}")
                        
            except asyncio.TimeoutError:
                if attempt < self.max_retries - 1:
                    delay = self._exponential_backoff(attempt)
                    logger.warning(f"Bybit timeout on attempt {attempt+1}, retrying in {delay:.2f}s...")
                    await asyncio.sleep(delay)
                    continue
                raise
            except Exception as e:
                if attempt < self.max_retries - 1:
                    delay = self._exponential_backoff(attempt)
                    logger.warning(f"Bybit error on attempt {attempt+1}, retrying in {delay:.2f}s: {e}")
                    await asyncio.sleep(delay)
                    continue
                raise
        
        raise Exception("All retries failed")
    
    def _exponential_backoff(self, attempt: int, base: Optional[float] = None, max_delay: float = 30) -> float:
        """Calculate exponential backoff with jitter using configurable backoff multiplier."""
        if base is None:
            base = self.retry_base
        # Apply backoff multiplier from settings
        backoff_multiplier = self.settings.backfill_retry_backoff
        delay = base * (backoff_multiplier ** attempt)
        delay = min(delay, max_delay)
        jitter = delay * 0.2 * (random.random() - 0.5)
        return delay + jitter
    
    def get_throttle_multiplier(self) -> float:
        """Get current throttle multiplier for dynamic request delay adjustment."""
        return self.throttle_multiplier
    
    def get_recommended_concurrency(self, base_concurrency: int) -> int:
        """Get recommended concurrency based on current throttle state."""
        # Reduce concurrency based on throttle multiplier
        if self.throttle_multiplier > 2.0:
            # Heavy throttling
            return max(1, base_concurrency // 4)
        elif self.throttle_multiplier > 1.5:
            # Moderate throttling
            return max(1, base_concurrency // 2)
        else:
            return base_concurrency


class BybitConnectorHistory:
    """Paginated loader for Bybit trades using the hftbacktest REST wrapper."""

    def __init__(
        self,
        settings: Settings,
        *,
        limit: int = 1000,
        request_delay: Optional[float] = None,
        max_retries: int = 5,
        chunk_minutes: int = 10,
        max_concurrent_chunks: Optional[int] = None,
        max_iterations_per_chunk: int = 500,
    ) -> None:
        self.settings = settings
        self.limit = max(1, limit)
        self._max_retries = max(0, max_retries)
        self.chunk_minutes = max(1, chunk_minutes)
        self.test_mode = settings.context_backfill_test_mode
        
        # Use higher concurrency when authentication is enabled
        api_key = settings.bybit_api_key
        api_secret = settings.bybit_api_secret
        use_auth = bool(api_key and api_secret)
        
        if self.test_mode:
            # Test mode: single window, serial execution, detailed logging
            self.max_concurrent_chunks = 1
            self.request_delay = 0.0  # No delay needed for single request
            logger.info("Bybit backfill: TEST MODE - single window serial execution")
        elif use_auth:
            # Aggressive parallelization with auth (higher rate limits)
            self.max_concurrent_chunks = max_concurrent_chunks or settings.bybit_backfill_max_concurrent_chunks
            self.request_delay = 0.0  # No delay needed with auth
            logger.info(f"Bybit backfill: Using authenticated mode with {self.max_concurrent_chunks} concurrent chunks")
        else:
            # Conservative settings for public endpoints
            self.max_concurrent_chunks = max_concurrent_chunks or max(1, settings.bybit_backfill_max_concurrent_chunks // 2)
            # Use configured public delay or provided request_delay
            if request_delay is not None:
                self.request_delay = max(0.0, request_delay)
            else:
                # Convert milliseconds to seconds from settings
                self.request_delay = settings.bybit_backfill_public_delay_ms / 1000.0
            logger.info(f"Bybit backfill: Using public mode with {self.max_concurrent_chunks} concurrent chunks, {self.request_delay*1000:.0f}ms delay")
            
        self.max_iterations_per_chunk = max(1, max_iterations_per_chunk)
        self.http_client = BybitHttpClient(settings)
        
        # Initialize cache manager if caching is enabled
        self.cache_enabled = settings.backfill_cache_enabled
        self.cache_manager: Optional[BackfillCacheManager] = None
        if self.cache_enabled:
            self.cache_manager = BackfillCacheManager(settings.backfill_cache_dir)

    async def test_single_window(self) -> List[TradeTick]:
        """Test authentication with a single 1-hour window."""
        from datetime import datetime, timezone
        
        # Test window: 2025-11-06T00:00:00 to 2025-11-06T01:00:00 UTC
        start_dt = datetime(2025, 11, 6, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(2025, 11, 6, 1, 0, 0, tzinfo=timezone.utc)
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)
        
        logger.info("=== BYBIT AUTHENTICATION TEST MODE ===")
        logger.info("Test mode: fetching single 1-hour window")
        logger.info(f"  Window: {start_dt.isoformat()} to {end_dt.isoformat()}")
        logger.info(f"  Timestamp: {start_ts} - {end_ts}")
        
        if self.http_client.use_auth:
            logger.info("  Using authenticated endpoints")
        else:
            logger.warning("  WARNING: No API credentials configured, using public endpoints")
        
        try:
            trades = await self._fetch_trades_paginated(start_dt, end_dt)
            logger.info(f"Test result: {len(trades)} trades loaded from test window")
            
            if trades:
                # Calculate partial VWAP and POC for verification
                vwap = sum(trade.price * trade.qty for trade in trades) / sum(trade.qty for trade in trades)
                
                # Calculate POC (Point of Control) using proper price binning
                price_volumes = {}
                for trade in trades:
                    # Use proper tick size binning instead of round(..., 3)
                    price_binned = quantize_price_to_tick(
                        trade.price,
                        None,  # We don't have exchange info in test mode
                        self.settings.profile_tick_size,
                        self.settings.symbol,
                    )
                    price_volumes[price_binned] = price_volumes.get(price_binned, 0) + trade.qty
                
                poc_price = max(price_volumes, key=price_volumes.get) if price_volumes else 0.0
                
                logger.info(f"VWAP (partial): {vwap:.2f}")
                logger.info(f"POCd (partial): {poc_price:.2f}")
                logger.info("✅ Success! Bybit authentication working correctly")
                logger.info("Ready to expand to full backfill...")
            else:
                logger.warning("⚠️  No trades loaded - may indicate an issue")
            
            return trades
            
        except Exception as e:
            logger.error(f"❌ Test failed: {e}")
            if self.http_client.use_auth:
                logger.error("Check your BYBIT_API_KEY and BYBIT_API_SECRET environment variables")
            raise

    async def backfill_with_cache(self, start_dt: datetime, end_dt: datetime) -> List[TradeTick]:
        """Backfill with smart cache resume strategy.
        
        Checks for existing cache and only downloads new data since last cached timestamp.
        Falls back to full backfill if no cache exists.
        
        Args:
            start_dt: Start datetime (should be day start, e.g., 00:00 UTC).
            end_dt: End datetime (current time).
            
        Returns:
            List of deduplicated trades sorted by timestamp.
        """
        if not self.cache_manager:
            # Caching disabled, do full backfill
            logger.info("Bybit backfill cache: disabled")
            return await self._backfill_parallel(start_dt, end_dt)
        
        today = start_dt.date()
        cache_path = self.cache_manager.get_cache_path(start_dt)
        
        # Try to load cached trades
        cached_trades_dicts = self.cache_manager.load_cached_trades(start_dt)
        
        if cached_trades_dicts:
            # Cache hit - determine if we need to download new data
            duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
            total_chunks = max(1, (duration_minutes + 9) // 10)
            logger.info(
                f"Bybit backfill cache: HIT ({len(cached_trades_dicts)} trades from {today.isoformat()}, "
                f"expected {total_chunks} chunks for full day)"
            )
            
            # Convert dict trades back to TradeTick objects
            cached_trades = self._dicts_to_trade_ticks(cached_trades_dicts)
            
            # Get the last cached timestamp
            last_cached_ts_ms = self.cache_manager.get_last_cached_timestamp(cached_trades_dicts)
            
            if last_cached_ts_ms is None:
                logger.warning("Could not extract timestamp from cached trades, using cache as-is")
                return cached_trades
            
            # Convert milliseconds to datetime
            last_cached_dt = datetime.fromtimestamp(last_cached_ts_ms / 1000, tz=timezone.utc)
            
            # Calculate gap since last cache
            gap_seconds = (end_dt - last_cached_dt).total_seconds()
            gap_minutes = gap_seconds / 60
            
            if gap_seconds <= 60:  # Less than 1 minute gap
                logger.info(f"Cache is fresh (gap: {gap_seconds:.0f}s), using as-is")
                return cached_trades
            else:
                gap_chunks = max(1, int((gap_minutes + 9) // 10))
                logger.info(
                    f"Gap detected: {gap_minutes:.1f} min (~{gap_chunks} chunks) since last cache. "
                    f"Downloading new data..."
                )
                
                # Download new data from last cached time to end_dt
                # Add 1ms buffer to avoid re-downloading the last cached trade
                new_start_dt = datetime.fromtimestamp(
                    (last_cached_ts_ms + 1) / 1000, tz=timezone.utc
                )
                
                # Only download if there's a gap
                if new_start_dt < end_dt:
                    new_trades = await self._backfill_parallel(new_start_dt, end_dt)
                    
                    # Merge old and new trades
                    all_trades_list = [
                        self._trade_tick_to_dict(t) for t in cached_trades
                    ] + [
                        self._trade_tick_to_dict(t) for t in new_trades
                    ]
                    
                    # Deduplicate
                    all_trades_list = self.cache_manager.deduplicate_trades(all_trades_list)
                    
                    # Convert back to TradeTick
                    all_trades = self._dicts_to_trade_ticks(all_trades_list)
                    
                    logger.info(
                        f"Downloaded {len(new_trades)} new trades, "
                        f"merged with {len(cached_trades)} cached trades, "
                        f"total: {len(all_trades)} after dedup"
                    )
                else:
                    logger.info("No gap to fill, using cached data")
                    all_trades = cached_trades
        else:
            # No cache - do full backfill
            duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
            chunk_count = max(1, (duration_minutes + 9) // 10)  # Round up to nearest 10min chunk
            logger.info(f"Bybit backfill cache: MISS, downloading {chunk_count} chunks for {duration_minutes} minutes")
            all_trades = await self._backfill_parallel(start_dt, end_dt)
        
        # Save to cache (always update to include latest data)
        all_trades_dicts = [self._trade_tick_to_dict(t) for t in all_trades]
        self.cache_manager.save_trades_to_cache(all_trades_dicts, start_dt)
        
        return all_trades

    def _trade_tick_to_dict(self, trade: TradeTick) -> Dict[str, Any]:
        """Convert TradeTick object to dictionary for cache storage."""
        return {
            "T": int(trade.ts.timestamp() * 1000),  # timestamp in milliseconds
            "i": str(trade.id),  # trade ID as string (Bybit uses string IDs)
            "p": float(trade.price),  # price
            "q": float(trade.qty),  # qty
            "s": trade.side,  # side
            "m": trade.isBuyerMaker,  # isBuyerMaker
        }

    def _dicts_to_trade_ticks(self, trades_dicts: List[Dict[str, Any]]) -> List[TradeTick]:
        """Convert dictionary trades to TradeTick objects."""
        trades = []
        for trade_dict in trades_dicts:
            try:
                # Map dict fields to TradeTick
                ts_ms = trade_dict.get("T", 0)
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                
                tick = TradeTick(
                    ts=ts,
                    price=float(trade_dict.get("p", 0)),
                    qty=float(trade_dict.get("q", 0)),
                    side=trade_dict.get("s", "buy"),
                    isBuyerMaker=bool(trade_dict.get("m", False)),
                    id=int(trade_dict.get("i", 0)),  # Convert string ID to int if needed
                )
                trades.append(tick)
            except Exception as e:
                logger.warning(f"Failed to convert Bybit trade dict: {e}, skipping")
                continue
        
        return trades

    async def iterate_trades(self, start: datetime, end: datetime) -> AsyncIterator[TradeTick]:
        # If in test mode, ignore the provided start/end and use test window
        if self.test_mode:
            logger.info("Bybit test mode active: using predefined 1-hour test window")
            trades = await self.test_single_window()
            for trade in trades:
                yield trade
            return
        
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
        """Split time window into chunks and download in parallel with throttling and dynamic concurrency adjustment."""
        import time
        start_time = time.time()
        
        chunks = self._split_time_range(start_dt, end_dt, self.chunk_minutes)
        
        # Track failed chunks for retry and adaptive concurrency
        failed_chunk_indices = []
        rate_limit_errors = 0
        # Use adaptive concurrency: start with configured value, reduce to 1 if rate limit errors spike
        base_concurrency = self.max_concurrent_chunks
        current_concurrency = base_concurrency
        current_request_delay = self.request_delay
        
        logger.info(
            "Bybit backfill: %d chunks (%d min each), adaptive concurrency (start: %d) from %s to %s",
            len(chunks),
            self.chunk_minutes,
            current_concurrency,
            start_dt.isoformat(),
            end_dt.isoformat(),
        )
        
        semaphore = asyncio.Semaphore(current_concurrency)
        
        async def fetch_chunk_throttled(chunk_index: int, chunk_start: datetime, chunk_end: datetime) -> Tuple[int, List[TradeTick], bool]:
            nonlocal rate_limit_errors, current_concurrency, semaphore
            async with semaphore:
                # Adjust delay based on rate limit pressure
                throttle_multiplier = self.http_client.get_throttle_multiplier()
                adjusted_delay = current_request_delay * throttle_multiplier
                if adjusted_delay > 0:
                    await asyncio.sleep(adjusted_delay + (random.random() * 0.05))
                try:
                    trades = await self._fetch_trades_paginated(chunk_start, chunk_end)
                    return chunk_index, trades, True
                except Exception as exc:
                    # Check for rate limit errors and adapt concurrency
                    if "429" in str(exc) or "10001" in str(exc):
                        rate_limit_errors += 1
                        if rate_limit_errors >= 3 and current_concurrency > 1:
                            logger.warning(
                                "Bybit rate limit errors detected (%d), reducing concurrency from %d to 1",
                                rate_limit_errors, current_concurrency
                            )
                            current_concurrency = 1
                            semaphore = asyncio.Semaphore(current_concurrency)
                    
                    logger.warning(
                        "Bybit chunk %d failed: %s to %s - %s, continuing...",
                        chunk_index,
                        chunk_start.isoformat(),
                        chunk_end.isoformat(),
                        exc,
                    )
                    return chunk_index, [], False
        
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
                logger.error("Bybit backfill chunk exception: %s", result)
                continue
            
            chunk_index, chunk_trades, was_successful = result
            if was_successful and chunk_trades:
                successful_chunks += 1
            elif not was_successful:
                failed_chunks += 1
                failed_chunk_indices.append(chunk_index)
                
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
                throttle_mult = self.http_client.get_throttle_multiplier()
                logger.info(
                    "Bybit progress: %d/%d chunks processed, ~%d trades, ~%.0fs remaining (throttle: %.1f%s, concurrency: %d)",
                    chunk_index + 1,
                    len(chunks),
                    len(all_trades),
                    eta_seconds,
                    throttle_mult,
                    "x" if throttle_mult > 1.0 else "",
                    current_concurrency,
                )
        
        # Retry failed chunks if any
        if failed_chunk_indices:
            logger.info("Retrying %d failed Bybit chunks...", len(failed_chunk_indices))
            retry_tasks = []
            for idx in failed_chunk_indices:
                c_start, c_end = chunks[idx]
                retry_tasks.append(fetch_chunk_throttled(idx, c_start, c_end))
            
            retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
            for result in retry_results:
                if isinstance(result, Exception):
                    logger.error("Bybit backfill chunk retry exception: %s", result)
                    continue
                
                chunk_index, chunk_trades, was_successful = result
                if was_successful and chunk_trades:
                    successful_chunks += 1
                    failed_chunks -= 1
                
                for trade in chunk_trades:
                    if trade.id not in seen_ids:
                        all_trades.append(trade)
                        seen_ids.add(trade.id)
        
        # Sort by timestamp to ensure chronological order
        all_trades.sort(key=lambda t: t.ts)
        
        # Safety check: warn if too many trades for the window
        window_hours = (end_dt - start_dt).total_seconds() / 3600
        if len(all_trades) > 500000 and window_hours < 24:
            logger.warning(
                "Bybit backfill sanity check: %d trades for %.1f hour window (expected ~50-150k)",
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
            # Group trades by price level using proper tick size binning
            price_volumes = {}
            for trade in all_trades:
                # Use proper tick size binning instead of round(..., 3)
                price_binned = quantize_price_to_tick(
                    trade.price,
                    None,  # We don't have exchange info in backfill
                    self.settings.profile_tick_size,
                    self.settings.symbol,
                )
                price_volumes[price_binned] = price_volumes.get(price_binned, 0) + trade.qty
            
            # Find price with maximum volume
            if price_volumes:
                poc_price = max(price_volumes, key=price_volumes.get)
        
        logger.info(
            "Bybit backfill complete: ~%d trades in %.1fs, %.1f%% chunks successful, VWAP=%.2f, POC=%.2f",
            len(all_trades),
            elapsed_time,
            success_rate,
            vwap,
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
        """Fetch trades for a time window with pagination logic."""
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        
        trades = []
        iteration = 0
        total_retries = 0
        
        while start_ms <= end_ms and iteration < self.max_iterations_per_chunk:
            try:
                # Use authenticated endpoint if available, otherwise public
                if self.http_client.use_auth:
                    payload = await self.http_client.fetch_private_trades(
                        symbol=self.settings.symbol,
                        start_time=start_ms,
                        end_time=end_ms,
                        limit=self.limit,
                    )
                else:
                    payload = await self.http_client.fetch_public_trades(
                        symbol=self.settings.symbol,
                        start_time=start_ms,
                        end_time=end_ms,
                        limit=self.limit,
                    )
                
                if not isinstance(payload, list) or not payload:
                    break
                
                # Parse trades
                for raw in payload:
                    try:
                        tick = self._parse_bybit_trade(raw)
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.debug("bybit_backfill_parse_skip error=%s payload=%s", exc, raw)
                        continue
                    
                    # Filter by time window (API might return slightly out-of-bounds trades)
                    if tick.ts < start_dt or tick.ts > end_dt:
                        continue
                    
                    trades.append(tick)
                
                # Check if we got all trades in this batch
                if len(payload) < self.limit:
                    break
                
                # For Bybit, use the last trade's timestamp + 1ms for pagination
                last_trade = payload[-1]
                last_ts = int(last_trade.get("time", 0))
                if last_ts == 0:
                    break
                
                start_ms = last_ts + 1
                
                # Safety check: if we've reached the end of the window
                if start_ms >= end_ms:
                    break
                
                iteration += 1
                
                # Progress logging every 10 iterations (not every trade)
                if iteration % 10 == 0:
                    logger.info(
                        "Bybit backfill chunk progress: %d trades loaded, %d iterations, %d retries",
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
                        "Bybit backfill failed for window %s to %s after %d retries: %s",
                        start_dt.isoformat(),
                        end_dt.isoformat(),
                        total_retries,
                        e,
                    )
                    raise
                logger.warning(
                    "Bybit backfill retry %d for window %s to %s: %s",
                    total_retries,
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                    e,
                )
                await asyncio.sleep(self.http_client._exponential_backoff(total_retries))
        
        # Safety limit check
        if iteration >= self.max_iterations_per_chunk - 1:
            logger.error(
                "Bybit backfill safety limit reached for window %s to %s: %d iterations, %d trades",
                start_dt.isoformat(),
                end_dt.isoformat(),
                iteration,
                len(trades),
            )
        
        return trades

    def _parse_bybit_trade(self, raw: dict) -> TradeTick:
        """Parse a Bybit trade into TradeTick model.
        
        Bybit public trade format:
        {
            "execId": "string",
            "symbol": "string", 
            "price": "string",
            "size": "string",
            "side": "Buy" or "Sell",
            "time": "string" (timestamp in milliseconds),
            "isBlockTrade": false
        }
        
        Bybit private trade format:
        {
            "symbol": "string",
            "execId": "string",
            "orderLinkId": "string",
            "orderId": "string",
            "side": "Buy" or "Sell",
            "orderPrice": "string",
            "orderQty": "string",
            "execType": "Trade",
            "execQty": "string",
            "execPrice": "string",
            "execFee": "string",
            "execTime": "string" (timestamp in milliseconds),
            "leavesQty": "string",
            "closedSize": "string"
        }
        """
        # Handle both public and private trade formats
        if "execPrice" in raw:
            # Private trade format
            price_str = raw.get("execPrice", "0")
            qty_str = raw.get("execQty", "0")
            side_str = raw.get("side", "")
            timestamp_str = raw.get("execTime", "0")
            trade_id_str = raw.get("execId", "0")
        else:
            # Public trade format
            price_str = raw.get("price", "0")
            qty_str = raw.get("size", "0")
            side_str = raw.get("side", "")
            timestamp_str = raw.get("time", "0")
            trade_id_str = raw.get("execId", "0")
        
        # Parse values
        price = float(price_str)
        qty = float(qty_str)
        side = "buy" if side_str.upper() == "BUY" else "sell"
        
        # Parse timestamp (milliseconds)
        timestamp_ms = int(timestamp_str)
        ts = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        
        # Convert trade ID to int (use hash if it's a string)
        try:
            trade_id = int(trade_id_str)
        except ValueError:
            # Use hash of string ID as fallback
            trade_id = hash(trade_id_str) & 0x7FFFFFFF  # Ensure positive
        
        # Determine if buyer is maker
        # For Bybit, if side is "Buy", then buyer is the taker (not maker)
        is_buyer_maker = side == "sell"
        
        return TradeTick(
            ts=ts,
            price=price,
            qty=qty,
            side=side,
            isBuyerMaker=is_buyer_maker,
            id=trade_id,
        )

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class BinanceHttpClient:
    """HTTP client for Binance API with proper headers, session management, retry logic, optional HMAC authentication, and circuit breaker."""
    
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
        
        # Circuit breaker state for rate limit management
        self.circuit_state = CircuitBreakerState.CLOSED
        self.consecutive_rate_limit_errors = 0
        self.rate_limit_threshold = settings.backfill_rate_limit_threshold
        self.cooldown_seconds = settings.backfill_cooldown_seconds
        self.cooldown_until: Optional[float] = None
        
        # Rate limit pressure indicators
        self.throttle_multiplier = 1.0  # Start at normal speed
        self.last_rate_limit_ts: Optional[float] = None
        
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
    
    async def check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is open and enforce cooldown if needed. Returns True if breaker is open."""
        if self.circuit_state != CircuitBreakerState.OPEN:
            return False
        
        if self.cooldown_until is None:
            return False
        
        current_time = time.time()
        if current_time < self.cooldown_until:
            remaining = self.cooldown_until - current_time
            logger.warning(f"Circuit breaker open: cooldown active for {remaining:.1f}s more")
            await asyncio.sleep(min(remaining, 1.0))  # Sleep up to 1 second
            return True
        
        # Cooldown expired, attempt recovery
        self.circuit_state = CircuitBreakerState.HALF_OPEN
        self.consecutive_rate_limit_errors = 0
        logger.info("Circuit breaker: entering HALF_OPEN state to test recovery")
        return False
    
    def on_rate_limit_error(self) -> None:
        """Handle a rate limit error (418/429/451)."""
        self.consecutive_rate_limit_errors += 1
        self.last_rate_limit_ts = time.time()
        
        # Increase throttle multiplier (will slow down requests)
        self.throttle_multiplier = min(5.0, self.throttle_multiplier * 1.5)
        
        if self.consecutive_rate_limit_errors >= self.rate_limit_threshold:
            if self.circuit_state != CircuitBreakerState.OPEN:
                self.circuit_state = CircuitBreakerState.OPEN
                self.cooldown_until = time.time() + self.cooldown_seconds
                logger.error(
                    f"Circuit breaker opened: {self.consecutive_rate_limit_errors} consecutive rate limit errors. "
                    f"Enforcing {self.cooldown_seconds}s cooldown (throttle_multiplier={self.throttle_multiplier:.1f}x)"
                )
    
    def on_successful_request(self) -> None:
        """Handle a successful request."""
        self.consecutive_rate_limit_errors = 0
        
        # Gradually recover throttle multiplier (progressive recovery)
        if self.throttle_multiplier > 1.0:
            self.throttle_multiplier = max(1.0, self.throttle_multiplier * 0.95)
        
        if self.circuit_state == CircuitBreakerState.HALF_OPEN:
            self.circuit_state = CircuitBreakerState.CLOSED
            logger.info("Circuit breaker: recovery successful, returning to CLOSED state")
    
    async def fetch_agg_trades(
        self,
        symbol: str,
        start_time: int,
        end_time: int,
        limit: int = 1000
    ) -> List[dict]:
        """Fetch aggregated trades with retry logic, optional HMAC authentication, and circuit breaker."""
        await self.connect()
        
        # Check circuit breaker state
        await self.check_circuit_breaker()
        
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
            
            # Debug logging for test mode
            if self.settings.context_backfill_test_mode:
                logger.info(f"HTTP Request: GET {url}")
                logger.info(f"  Params: symbol={params['symbol']}, startTime={params['startTime']}, endTime={params['endTime']}, limit={params['limit']}")
                logger.info(f"  Auth: timestamp={params['timestamp']}, recvWindow={params['recvWindow']}")
                sig_preview = signature[:20] + "..." if len(signature) > 20 else signature
                logger.info(f"  Signature: {sig_preview}")
        
        rate_limit_error_count = 0
        for attempt in range(self.max_retries):
            try:
                async with self.session.get(url, params=params) as resp:
                    if self.settings.context_backfill_test_mode:
                        logger.info(f"HTTP Response: {resp.status} {resp.reason}")
                        
                    if resp.status == 200:
                        self.on_successful_request()
                        return await resp.json()
                    elif resp.status == 401:
                        # Unauthorized - likely bad credentials
                        logger.error("Binance API authentication failed (401). Check your API credentials.")
                        if attempt == 0 and self.use_auth:
                            logger.info("Falling back to public endpoints for subsequent requests...")
                            self.use_auth = False
                            # Remove auth params and retry
                            params.pop("timestamp", None)
                            params.pop("recvWindow", None)
                            params.pop("signature", None)
                            if "X-MBX-APIKEY" in self.headers:
                                del self.headers["X-MBX-APIKEY"]
                            continue
                        else:
                            raise Exception("Authentication failed and fallback unsuccessful")
                    elif resp.status == 403:
                        # Forbidden - API key restrictions, fallback to public mode
                        logger.warning("Binance API access forbidden (403). Attempting fallback to public endpoints...")
                        if self.use_auth:
                            self.use_auth = False
                            params.pop("timestamp", None)
                            params.pop("recvWindow", None)
                            params.pop("signature", None)
                            if "X-MBX-APIKEY" in self.headers:
                                del self.headers["X-MBX-APIKEY"]
                            if attempt < self.max_retries - 1:
                                await asyncio.sleep(0.5)
                                continue
                        if attempt < self.max_retries - 1:
                            delay = self._exponential_backoff(attempt)
                            logger.warning(f"Retrying in {delay:.2f}s (attempt {attempt+1}/{self.max_retries})")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            raise Exception(f"API access forbidden (403) after {self.max_retries} retries")
                    elif resp.status in {418, 429, 451}:
                        # Rate limited, blocked, or geographic restriction
                        rate_limit_error_count += 1
                        self.on_rate_limit_error()
                        
                        if self.settings.context_backfill_test_mode:
                            # In test mode, log full response details for debugging
                            logger.error(f"HTTP {resp.status} error in test mode!")
                            logger.error(f"Response headers: {dict(resp.headers)}")
                            try:
                                error_text = await resp.text()
                                logger.error(f"Response body: {error_text}")
                            except:
                                pass
                            logger.error(f"Request URL: {url}")
                            logger.error(f"Request params: {params}")
                        
                        # Check if we should switch to public mode for rate-limited auth requests
                        if self.use_auth and resp.status in {429, 418}:
                            logger.warning(
                                f"Rate limit detected on authenticated request (HTTP {resp.status}). "
                                f"Downgrading to public mode (consecutive errors: {self.consecutive_rate_limit_errors})"
                            )
                            self.use_auth = False
                            params.pop("timestamp", None)
                            params.pop("recvWindow", None)
                            params.pop("signature", None)
                            if "X-MBX-APIKEY" in self.headers:
                                del self.headers["X-MBX-APIKEY"]
                        
                        if attempt < self.max_retries - 1:
                            delay = self._exponential_backoff(attempt)
                            # Apply throttle multiplier to delay
                            adjusted_delay = delay * self.throttle_multiplier
                            logger.warning(
                                f"HTTP {resp.status} error (attempt {attempt+1}/{self.max_retries}), "
                                f"retrying in {adjusted_delay:.2f}s (throttle: {self.throttle_multiplier:.1f}x)"
                            )
                            await asyncio.sleep(adjusted_delay)
                            continue
                        else:
                            raise Exception(f"Max retries exceeded for {url} (HTTP {resp.status}, {rate_limit_error_count} rate limit errors)")
                    else:
                        logger.error(f"Unexpected status {resp.status}")
                        if self.settings.context_backfill_test_mode:
                            try:
                                error_text = await resp.text()
                                logger.error(f"Response body: {error_text}")
                            except:
                                pass
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
        """Calculate exponential backoff with jitter using configurable backoff multiplier."""
        if base is None:
            base = self.retry_base
        # Apply backoff multiplier from settings
        backoff_multiplier = self.settings.backfill_retry_backoff
        delay = base * (backoff_multiplier ** attempt)
        delay = min(delay, max_delay)
        jitter = delay * 0.2 * (random.random() - 0.5)
        return delay + jitter
    
    def get_throttle_multiplier(self) -> float:
        """Get current throttle multiplier for dynamic request delay adjustment."""
        return self.throttle_multiplier
    
    def get_recommended_concurrency(self, base_concurrency: int) -> int:
        """Get recommended concurrency based on current throttle state."""
        # Reduce concurrency based on throttle multiplier
        if self.throttle_multiplier > 2.0:
            # Heavy throttling
            return max(1, base_concurrency // 4)
        elif self.throttle_multiplier > 1.5:
            # Moderate throttling
            return max(1, base_concurrency // 2)
        else:
            return base_concurrency


class BinanceTradeHistory:
    """Paginated loader for Binance aggregated trades using the REST API."""

    def __init__(
        self,
        settings: Settings,
        *,
        limit: int = 1000,
        request_delay: Optional[float] = None,
        max_retries: int = 5,
        chunk_minutes: int = 10,
        max_concurrent_chunks: int = 3,  # Reduced default for adaptive behavior
        max_iterations_per_chunk: int = 500,
    ) -> None:
        self.settings = settings
        self.limit = max(1, limit)
        self._max_retries = max(0, max_retries)
        self.chunk_minutes = max(1, chunk_minutes)
        self.test_mode = settings.context_backfill_test_mode
        
        # Use higher concurrency when authentication is enabled
        api_key = settings.binance_api_key
        api_secret = settings.binance_api_secret
        use_auth = bool(api_key and api_secret)
        
        if self.test_mode:
            # Test mode: single window, serial execution, detailed logging
            self.max_concurrent_chunks = 1
            self.request_delay = 0.0  # No delay needed for single request
            logger.info("Backfill: TEST MODE - single window serial execution")
        elif use_auth:
            # Aggressive parallelization with auth (higher rate limits)
            self.max_concurrent_chunks = 20
            self.request_delay = 0.0  # No delay needed with auth
            logger.info("Backfill: Using authenticated mode with 20 concurrent chunks")
        else:
            # Conservative settings for public endpoints
            self.max_concurrent_chunks = max(1, max_concurrent_chunks)
            # Use configured public delay or provided request_delay
            if request_delay is not None:
                self.request_delay = max(0.0, request_delay)
            else:
                # Convert milliseconds to seconds from settings
                self.request_delay = settings.backfill_public_delay_ms / 1000.0
            logger.info(f"Backfill: Using public mode with {self.max_concurrent_chunks} concurrent chunks, {self.request_delay*1000:.0f}ms delay")
            
        self.max_iterations_per_chunk = max(1, max_iterations_per_chunk)
        self.http_client = BinanceHttpClient(settings)
        
        # Initialize cache manager if caching is enabled
        self.cache_enabled = settings.backfill_cache_enabled
        self.cache_manager: Optional[BackfillCacheManager] = None
        if self.cache_enabled:
            self.cache_manager = BackfillCacheManager(settings.backfill_cache_dir)

    async def test_single_window(self) -> List[TradeTick]:
        """Test HMAC authentication with a single 1-hour window."""
        from datetime import datetime, timezone
        
        # Test window: 2025-11-06T00:00:00 to 2025-11-06T01:00:00 UTC
        start_dt = datetime(2025, 11, 6, 0, 0, 0, tzinfo=timezone.utc)
        end_dt = datetime(2025, 11, 6, 1, 0, 0, tzinfo=timezone.utc)
        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)
        
        logger.info("=== HMAC AUTHENTICATION TEST MODE ===")
        logger.info("Test mode: fetching single 1-hour window")
        logger.info(f"  Window: {start_dt.isoformat()} to {end_dt.isoformat()}")
        logger.info(f"  Timestamp: {start_ts} - {end_ts}")
        
        if self.http_client.use_auth:
            # Log signature preview for debugging
            test_params = {
                "symbol": self.settings.symbol,
                "startTime": start_ts,
                "endTime": end_ts,
                "limit": 1000,
                "timestamp": int(time.time() * 1000),
                "recvWindow": 5000
            }
            signature = self.http_client._sign_request(test_params)
            sig_preview = signature[:20] + "..." if len(signature) > 20 else signature
            logger.info(f"  Signature preview: {sig_preview}")
        else:
            logger.warning("  WARNING: No API credentials configured, using public endpoints")
        
        try:
            trades = await self._fetch_trades_paginated(start_dt, end_dt)
            logger.info(f"Test result: {len(trades)} trades loaded from test window")
            
            if trades:
                # Calculate partial VWAP and POC for verification
                vwap = sum(trade.price * trade.qty for trade in trades) / sum(trade.qty for trade in trades)
                
                # Calculate POC (Point of Control) using proper price binning
                price_volumes = {}
                for trade in trades:
                    # Use proper tick size binning instead of round(..., 3)
                    price_binned = quantize_price_to_tick(
                        trade.price,
                        None,  # We don't have exchange info in test mode
                        self.settings.profile_tick_size,
                        self.settings.symbol,
                    )
                    price_volumes[price_binned] = price_volumes.get(price_binned, 0) + trade.qty
                
                poc_price = max(price_volumes, key=price_volumes.get) if price_volumes else 0.0
                
                logger.info(f"VWAP (partial): {vwap:.2f}")
                logger.info(f"POCd (partial): {poc_price:.2f}")
                logger.info("✅ Success! HMAC authentication working correctly")
                logger.info("Ready to expand to full backfill...")
            else:
                logger.warning("⚠️  No trades loaded - may indicate an issue")
            
            return trades
            
        except Exception as e:
            logger.error(f"❌ Test failed: {e}")
            if self.http_client.use_auth:
                logger.error("Check your BINANCE_API_KEY and BINANCE_API_SECRET environment variables")
            raise

    async def backfill_with_cache(self, start_dt: datetime, end_dt: datetime) -> List[TradeTick]:
        """Backfill with smart cache resume strategy.
        
        Checks for existing cache and only downloads new data since last cached timestamp.
        Falls back to full backfill if no cache exists.
        
        Args:
            start_dt: Start datetime (should be day start, e.g., 00:00 UTC).
            end_dt: End datetime (current time).
            
        Returns:
            List of deduplicated trades sorted by timestamp.
        """
        if not self.cache_manager:
            # Caching disabled, do full backfill
            logger.info("Backfill cache: disabled")
            return await self._backfill_parallel(start_dt, end_dt)
        
        today = start_dt.date()
        cache_path = self.cache_manager.get_cache_path(start_dt)
        
        # Try to load cached trades
        cached_trades_dicts = self.cache_manager.load_cached_trades(start_dt)
        
        if cached_trades_dicts:
            # Cache hit - determine if we need to download new data
            logger.info(f"Backfill cache: HIT ({len(cached_trades_dicts)} trades from {today.isoformat()})")
            
            # Convert dict trades back to TradeTick objects
            cached_trades = self._dicts_to_trade_ticks(cached_trades_dicts)
            
            # Get the last cached timestamp
            last_cached_ts_ms = self.cache_manager.get_last_cached_timestamp(cached_trades_dicts)
            
            if last_cached_ts_ms is None:
                logger.warning("Could not extract timestamp from cached trades, using cache as-is")
                return cached_trades
            
            # Convert milliseconds to datetime
            last_cached_dt = datetime.fromtimestamp(last_cached_ts_ms / 1000, tz=timezone.utc)
            
            # Calculate gap since last cache
            gap_hours = (end_dt - last_cached_dt).total_seconds() / 3600
            
            if gap_hours <= 0:
                logger.info(f"Cache is fresh (< 1h old, gap: {gap_hours:.1f}h), using as-is")
                return cached_trades
            else:
                logger.info(f"Gap detected: {gap_hours:.1f}h since last cache. Downloading new data...")
                
                # Download new data from last cached time to end_dt
                # Add 1ms buffer to avoid re-downloading the last cached trade
                new_start_dt = datetime.fromtimestamp(
                    (last_cached_ts_ms + 1) / 1000, tz=timezone.utc
                )
                
                # Only download if there's a gap
                if new_start_dt < end_dt:
                    new_trades = await self._backfill_parallel(new_start_dt, end_dt)
                    
                    # Merge old and new trades
                    all_trades_list = [
                        self._trade_tick_to_dict(t) for t in cached_trades
                    ] + [
                        self._trade_tick_to_dict(t) for t in new_trades
                    ]
                    
                    # Deduplicate
                    all_trades_list = self.cache_manager.deduplicate_trades(all_trades_list)
                    
                    # Convert back to TradeTick
                    all_trades = self._dicts_to_trade_ticks(all_trades_list)
                    
                    logger.info(
                        f"Downloaded {len(new_trades)} new trades, "
                        f"merged with {len(cached_trades)} cached trades, "
                        f"total: {len(all_trades)} after dedup"
                    )
                else:
                    logger.info("No gap to fill, using cached data")
                    all_trades = cached_trades
        else:
            # No cache - do full backfill
            duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
            chunk_count = max(1, (duration_minutes + 9) // 10)  # Round up to nearest 10min chunk
            logger.info(f"Backfill cache: MISS, downloading {chunk_count} chunks")
            all_trades = await self._backfill_parallel(start_dt, end_dt)
        
        # Save to cache (always update to include latest data)
        all_trades_dicts = [self._trade_tick_to_dict(t) for t in all_trades]
        self.cache_manager.save_trades_to_cache(all_trades_dicts, start_dt)
        
        return all_trades

    def _trade_tick_to_dict(self, trade: TradeTick) -> Dict[str, Any]:
        """Convert TradeTick object to dictionary for cache storage."""
        return {
            "T": int(trade.ts.timestamp() * 1000),  # timestamp in milliseconds
            "a": trade.id,  # aggTradeId
            "p": float(trade.price),  # price
            "q": float(trade.qty),  # qty
            "f": 0,  # firstTradeId (not available, use 0)
            "l": 0,  # lastTradeId (not available, use 0)
            "m": trade.isBuyerMaker,  # isBuyerMaker
            "M": True,  # ignore (not used)
        }

    def _dicts_to_trade_ticks(self, trades_dicts: List[Dict[str, Any]]) -> List[TradeTick]:
        """Convert dictionary trades to TradeTick objects."""
        trades = []
        for trade_dict in trades_dicts:
            try:
                # Map dict fields to TradeTick
                ts_ms = trade_dict.get("T", 0)
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                
                tick = TradeTick(
                    ts=ts,
                    price=float(trade_dict.get("p", 0)),
                    qty=float(trade_dict.get("q", 0)),
                    side="buy" if not trade_dict.get("m") else "sell",  # isBuyerMaker reversed
                    isBuyerMaker=bool(trade_dict.get("m", False)),
                    id=int(trade_dict.get("a", 0)),  # aggTradeId as id
                )
                trades.append(tick)
            except Exception as e:
                logger.warning(f"Failed to convert trade dict: {e}, skipping")
                continue
        
        return trades

    async def iterate_trades(self, start: datetime, end: datetime) -> AsyncIterator[TradeTick]:
        # If in test mode, ignore the provided start/end and use test window
        if self.test_mode:
            logger.info("Test mode active: using predefined 1-hour test window")
            trades = await self.test_single_window()
            for trade in trades:
                yield trade
            return
        
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
        """Split time window into chunks and download in parallel with throttling and dynamic concurrency adjustment."""
        import time
        start_time = time.time()
        
        chunks = self._split_time_range(start_dt, end_dt, self.chunk_minutes)
        
        # Track failed chunks for retry and adaptive concurrency
        failed_chunk_indices = []
        rate_limit_errors = 0
        # Use adaptive concurrency: start with 3, reduce to 1 if 429 errors spike
        base_concurrency = 3
        current_concurrency = base_concurrency
        current_request_delay = self.request_delay
        
        logger.info(
            "Backfill: %d chunks (%d min each), adaptive concurrency (start: %d) from %s to %s",
            len(chunks),
            self.chunk_minutes,
            current_concurrency,
            start_dt.isoformat(),
            end_dt.isoformat(),
        )
        
        semaphore = asyncio.Semaphore(current_concurrency)
        
        async def fetch_chunk_throttled(chunk_index: int, chunk_start: datetime, chunk_end: datetime) -> Tuple[int, List[TradeTick], bool]:
            nonlocal rate_limit_errors, current_concurrency, semaphore
            async with semaphore:
                # Adjust delay based on rate limit pressure
                throttle_multiplier = self.http_client.get_throttle_multiplier()
                adjusted_delay = current_request_delay * throttle_multiplier
                if adjusted_delay > 0:
                    await asyncio.sleep(adjusted_delay + (random.random() * 0.05))
                try:
                    trades = await self._fetch_trades_paginated(chunk_start, chunk_end)
                    return chunk_index, trades, True
                except Exception as exc:
                    # Check for rate limit errors and adapt concurrency
                    if "429" in str(exc) or "418" in str(exc):
                        rate_limit_errors += 1
                        if rate_limit_errors >= 3 and current_concurrency > 1:
                            logger.warning(
                                "Rate limit errors detected (%d), reducing concurrency from %d to 1",
                                rate_limit_errors, current_concurrency
                            )
                            current_concurrency = 1
                            semaphore = asyncio.Semaphore(current_concurrency)
                    
                    logger.warning(
                        "Chunk %d failed: %s to %s - %s, continuing...",
                        chunk_index,
                        chunk_start.isoformat(),
                        chunk_end.isoformat(),
                        exc,
                    )
                    return chunk_index, [], False
        
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
            
            chunk_index, chunk_trades, was_successful = result
            if was_successful and chunk_trades:
                successful_chunks += 1
            elif not was_successful:
                failed_chunks += 1
                failed_chunk_indices.append(chunk_index)
                
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
                throttle_mult = self.http_client.get_throttle_multiplier()
                logger.info(
                    "Progress: %d/%d chunks processed, ~%d trades, ~%.0fs remaining (throttle: %.1f%s, concurrency: %d)",
                    chunk_index + 1,
                    len(chunks),
                    len(all_trades),
                    eta_seconds,
                    throttle_mult,
                    "x" if throttle_mult > 1.0 else "",
                    current_concurrency,
                )
        
        # Retry failed chunks if any
        if failed_chunk_indices:
            logger.info("Retrying %d failed chunks...", len(failed_chunk_indices))
            retry_tasks = []
            for idx in failed_chunk_indices:
                c_start, c_end = chunks[idx]
                retry_tasks.append(fetch_chunk_throttled(idx, c_start, c_end))
            
            retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
            for result in retry_results:
                if isinstance(result, Exception):
                    logger.error("Backfill chunk retry exception: %s", result)
                    continue
                
                chunk_index, chunk_trades, was_successful = result
                if was_successful and chunk_trades:
                    successful_chunks += 1
                    failed_chunks -= 1
                
                for trade in chunk_trades:
                    if trade.id not in seen_ids:
                        all_trades.append(trade)
                        seen_ids.add(trade.id)
        
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
            # Group trades by price level using proper tick size binning
            price_volumes = {}
            for trade in all_trades:
                # Use proper tick size binning instead of round(..., 3)
                price_binned = quantize_price_to_tick(
                    trade.price,
                    None,  # We don't have exchange info in backfill
                    self.settings.profile_tick_size,
                    self.settings.symbol,
                )
                price_volumes[price_binned] = price_volumes.get(price_binned, 0) + trade.qty
            
            # Find price with maximum volume
            if price_volumes:
                poc_price = max(price_volumes, key=price_volumes.get)
        
        logger.info(
            "Backfill complete: ~%d trades in %.1fs, %.1f%% chunks successful, VWAP=%.2f, POC=%.2f",
            len(all_trades),
            elapsed_time,
            success_rate,
            vwap,
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