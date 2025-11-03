"""Metrics helpers for websocket ingestion."""
from __future__ import annotations

import time
from collections import deque
from threading import Lock

from .models import MetricsSnapshot, MetricsView


class MetricsRecorder:
    """Fixed-window metrics for trade and depth streams."""

    def __init__(self, window_sec: int) -> None:
        self.window_sec = window_sec
        self._trade_events: deque[float] = deque()
        self._depth_events: deque[float] = deque()
        self._lock = Lock()

    def record_trade(self) -> None:
        """Record a trade event occurrence."""

        self._record(self._trade_events)

    def record_depth(self) -> None:
        """Record a depth event occurrence."""

        self._record(self._depth_events)

    def snapshot(self, trade_queue_size: int, depth_queue_size: int) -> MetricsSnapshot:
        """Return metrics for each stream within the configured window."""

        with self._lock:
            self._prune(self._trade_events)
            self._prune(self._depth_events)

            trade_count = len(self._trade_events)
            depth_count = len(self._depth_events)

        return MetricsSnapshot(
            trades=self._build_view(trade_count, trade_queue_size),
            depth=self._build_view(depth_count, depth_queue_size),
        )

    def _record(self, bucket: deque[float]) -> None:
        now = time.time()
        with self._lock:
            bucket.append(now)
            self._prune(bucket)

    def _prune(self, bucket: deque[float]) -> None:
        cutoff = time.time() - self.window_sec
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def _build_view(self, count: int, queue_size: int) -> MetricsView:
        per_second = count / self.window_sec if self.window_sec else 0.0
        return MetricsView(
            per_minute_count=count,
            per_second_rate=per_second,
            queue_size=queue_size,
        )
