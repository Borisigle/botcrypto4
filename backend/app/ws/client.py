"""Base websocket client utilities."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

import websockets
from websockets import WebSocketException

from .models import Settings, StreamHealth


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def structured_log(logger: logging.Logger, event: str, **fields: Any) -> None:
    payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event}
    payload.update(fields)
    logger.info(json.dumps(payload, default=_serialize))


class StreamState:
    """Mutable stream state shared with health endpoints."""

    def __init__(self) -> None:
        self.connected: bool = False
        self.last_ts: Optional[datetime] = None

    def snapshot(self) -> StreamHealth:
        return StreamHealth(connected=self.connected, last_ts=self.last_ts)


class BaseStreamService(ABC):
    """Generic websocket ingestion service with background tasks."""

    def __init__(self, name: str, url: str, settings: Settings) -> None:
        self.name = name
        self.url = url
        self.settings = settings
        self.logger = logging.getLogger(f"ws.{name}")
        self.state = StreamState()
        self.queue: Optional[asyncio.Queue[Any]] = None
        self._network_task: Optional[asyncio.Task[None]] = None
        self._processor_task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self.queue is not None:
            return
        self.queue = asyncio.Queue(maxsize=self.settings.max_queue)
        self._stop_event.clear()
        await self.on_start()
        self._network_task = asyncio.create_task(self._network_loop(), name=f"{self.name}-network")
        self._processor_task = asyncio.create_task(
            self._processor_loop(), name=f"{self.name}-processor"
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._network_task:
            self._network_task.cancel()
        if self._processor_task:
            self._processor_task.cancel()

        tasks = [t for t in (self._network_task, self._processor_task) if t]
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - defensive logging
                self.logger.exception("Unhandled exception during shutdown: %s", exc)

        self._network_task = None
        self._processor_task = None

        await self.on_stop()
        self.queue = None

    @property
    def queue_size(self) -> int:
        return self.queue.qsize() if self.queue else 0

    def health(self) -> StreamHealth:
        return self.state.snapshot()

    async def _network_loop(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=None,
                ) as ws:
                    self._handle_connected()
                    backoff = 1.0
                    async for raw in ws:
                        if self._stop_event.is_set() or self.queue is None:
                            break
                        try:
                            payload = self._decode_message(raw)
                        except json.JSONDecodeError as exc:
                            structured_log(
                                self.logger,
                                "decode_error",
                                error=str(exc),
                                stream=self.name,
                            )
                            continue
                        await self._enqueue(payload)
            except asyncio.CancelledError:
                raise
            except (OSError, WebSocketException) as exc:
                structured_log(
                    self.logger,
                    "ws_error",
                    stream=self.name,
                    error=str(exc),
                    reconnect_delay=round(backoff, 2),
                )
            finally:
                self._handle_disconnected()

            if self._stop_event.is_set():
                break

            sleep_for = min(backoff + random.uniform(0, 1.0), 10.0)
            await asyncio.sleep(sleep_for)
            backoff = min(backoff * 2, 15.0)

    async def _processor_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self.queue:
                await asyncio.sleep(0.25)
                continue
            try:
                payload = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            try:
                await self.handle_payload(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive logging
                structured_log(
                    self.logger,
                    "processor_error",
                    stream=self.name,
                    error=str(exc),
                )
            finally:
                self.queue.task_done()

    async def _enqueue(self, payload: Any) -> None:
        if not self.queue:
            return
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            dropped = None
            try:
                dropped = self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                pass
            structured_log(
                self.logger,
                "queue_backpressure",
                stream=self.name,
                action="drop_oldest",
                dropped=bool(dropped),
            )
            await self.queue.put(payload)

    def _decode_message(self, raw: Any) -> Any:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            return json.loads(raw)
        return raw

    def _handle_connected(self) -> None:
        if not self.state.connected:
            self.state.connected = True
            structured_log(self.logger, "ws_connected", stream=self.name, url=self.url)

    def _handle_disconnected(self) -> None:
        if self.state.connected:
            self.state.connected = False
            structured_log(self.logger, "ws_disconnected", stream=self.name)

    @abstractmethod
    async def handle_payload(self, payload: Any) -> None:
        """Process a decoded payload from the websocket stream."""

    async def on_start(self) -> None:
        """Hook for subclasses to perform async startup logic."""

    async def on_stop(self) -> None:
        """Hook for subclasses to perform async shutdown logic."""
