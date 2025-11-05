"""Historical trade backfill utilities for initializing context state."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Optional, Protocol

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
    ) -> None:
        self.settings = settings
        self.limit = max(1, limit)
        self.request_delay = max(0.0, request_delay)
        self._http_client_factory = http_client_factory or self._default_client_factory
        self._max_retries = max(0, max_retries)
        self._retry_base_delay = 0.25
        self._retry_max_delay = 5.0

    async def iterate_trades(self, start: datetime, end: datetime) -> AsyncIterator[TradeTick]:
        start_utc = self._ensure_utc(start)
        end_utc = self._ensure_utc(end)
        start_ms = int(start_utc.timestamp() * 1000)
        end_ms = int(end_utc.timestamp() * 1000)
        if start_ms > end_ms:
            return

        endpoint = f"{self.settings.rest_base_url.rstrip('/')}/fapi/v1/aggTrades"
        params_base = {
            "symbol": self.settings.symbol.upper(),
            "limit": self.limit,
        }

        async with self._http_client_factory() as client:
            next_start = start_ms
            while next_start <= end_ms:
                response = await self._request_with_retry(
                    client,
                    endpoint,
                    params_base,
                    start_time=next_start,
                    end_time=end_ms,
                )
                payload = response.json()
                if not isinstance(payload, list) or not payload:
                    break

                raw_last_ts = payload[-1].get("T")
                for raw in payload:
                    try:
                        tick = parse_trade_message(raw)
                    except Exception as exc:  # pragma: no cover - defensive logging
                        logger.debug("backfill_parse_skip error=%s payload=%s", exc, raw)
                        continue

                    if tick.ts < start_utc or tick.ts > end_utc:
                        continue
                    yield tick

                if raw_last_ts is None:
                    break

                last_ts = int(raw_last_ts)
                if len(payload) < self.limit or last_ts >= end_ms:
                    break

                next_start = max(last_ts + 1, next_start + 1)
                if next_start > end_ms:
                    break

                if self.request_delay > 0:
                    await asyncio.sleep(self.request_delay)

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
