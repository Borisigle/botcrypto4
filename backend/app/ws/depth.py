"""Order book depth ingestion for Binance perpetual futures."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from .client import BaseStreamService, structured_log
from .metrics import MetricsRecorder
from .models import DepthUpdate, PriceLevel, Settings


class DepthSyncError(RuntimeError):
    """Base exception for depth synchronization errors."""


class DepthGapError(DepthSyncError):
    """Raised when a sequence gap is detected in depth diffs."""


class DepthSynchronizer:
    """Handles snapshot loading and incremental depth diff application."""

    def __init__(self) -> None:
        self.last_update_id: Optional[int] = None
        self._ready: bool = False
        self._bids: Dict[str, float] = {}
        self._asks: Dict[str, float] = {}

    def load_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self.last_update_id = int(snapshot.get("lastUpdateId"))
        self._bids = {price: float(qty) for price, qty in snapshot.get("bids", [])}
        self._asks = {price: float(qty) for price, qty in snapshot.get("asks", [])}
        self._ready = False

    def apply_update(self, payload: Dict[str, Any]) -> Optional[DepthUpdate]:
        if self.last_update_id is None:
            raise DepthSyncError("snapshot not loaded")

        try:
            update_start = int(payload["U"])
            update_end = int(payload["u"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DepthSyncError("depth payload missing sequence ids") from exc

        expected = self.last_update_id + 1

        if update_end <= self.last_update_id:
            return None

        if not self._ready:
            if update_start <= expected <= update_end:
                self._ready = True
            else:
                return None
        else:
            if update_end < expected:
                return None
            if update_start > expected:
                raise DepthGapError(
                    f"Gap detected. Expected {expected}, received start {update_start}"
                )

        bids = self._update_side(self._bids, payload.get("b", []))
        asks = self._update_side(self._asks, payload.get("a", []))

        self.last_update_id = update_end

        event_time_ms = payload.get("E") or payload.get("T")
        if event_time_ms is None:
            raise DepthSyncError("depth payload missing event time")
        try:
            event_time_ms = int(event_time_ms)
        except (TypeError, ValueError) as exc:
            raise DepthSyncError("invalid depth event time") from exc

        ts = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc)
        return DepthUpdate(
            ts=ts,
            bids=[PriceLevel(price=price, qty=qty) for price, qty in bids],
            asks=[PriceLevel(price=price, qty=qty) for price, qty in asks],
            lastUpdateId=self.last_update_id,
        )

    @staticmethod
    def _update_side(
        book: Dict[str, float], updates: Iterable[Iterable[str]]
    ) -> List[Tuple[float, float]]:
        normalized: List[Tuple[float, float]] = []
        for price_str, qty_str in updates:
            price = float(price_str)
            qty = float(qty_str)
            if qty == 0:
                book.pop(price_str, None)
            else:
                book[price_str] = qty
            normalized.append((price, qty))
        return normalized


class DepthStream(BaseStreamService):
    """Background service streaming depth diffs with snapshot synchronization."""

    def __init__(self, settings: Settings, metrics: MetricsRecorder) -> None:
        super().__init__("depth", settings.depth_ws_url or "", settings)
        self.metrics = metrics
        self._sync = DepthSynchronizer()
        self._client: Optional[httpx.AsyncClient] = None

    async def on_start(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
        await self._refresh_snapshot()

    async def on_stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def handle_payload(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("e") != "depthUpdate":
            return
        try:
            update = self._sync.apply_update(payload)
        except DepthGapError as exc:
            structured_log(
                self.logger,
                "depth_gap_detected",
                details=str(exc),
            )
            await self._refresh_snapshot()
            return
        except DepthSyncError as exc:
            structured_log(
                self.logger,
                "depth_sync_error",
                error=str(exc),
            )
            return

        if not update:
            return

        self.state.last_ts = update.ts
        self.metrics.record_depth()
        lag_ms = (datetime.now(timezone.utc) - update.ts).total_seconds() * 1000
        structured_log(
            self.logger,
            "depth_update",
            lag_ms=round(lag_ms, 2),
            queue_size=self.queue_size,
            last_update_id=update.lastUpdateId,
            bids=len(update.bids),
            asks=len(update.asks),
        )

    async def _refresh_snapshot(self) -> None:
        if not self._client:
            raise DepthSyncError("HTTP client not initialized")

        endpoint = f"{self.settings.rest_base_url.rstrip('/')}/fapi/v1/depth"
        params = {"symbol": self.settings.symbol, "limit": self.settings.depth_snapshot_limit}

        attempt = 0
        while not self._stop_event.is_set() and attempt < 5:
            attempt += 1
            try:
                response = await self._client.get(endpoint, params=params)
                response.raise_for_status()
                snapshot = response.json()
                self._sync.load_snapshot(snapshot)
                await self._drain_queue()
                structured_log(
                    self.logger,
                    "depth_snapshot_loaded",
                    last_update_id=self._sync.last_update_id,
                    attempt=attempt,
                )
                return
            except (httpx.HTTPError, ValueError) as exc:
                delay = min(2 ** attempt, 10)
                structured_log(
                    self.logger,
                    "depth_snapshot_retry",
                    attempt=attempt,
                    delay=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)

        raise DepthSyncError("Unable to refresh order book snapshot after retries")

    async def _drain_queue(self) -> None:
        if not self.queue:
            return
        drained = 0
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            structured_log(
                self.logger,
                "depth_queue_drained",
                removed=drained,
            )
