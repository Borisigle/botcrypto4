"""Session scheduler for strategy component lifecycle management."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Optional

from .models import SessionState


class SessionScheduler:
    """Manages trading session windows and component activation."""

    # Session time windows (UTC)
    LONDON_START = time(hour=8, minute=0, tzinfo=timezone.utc)
    LONDON_END = time(hour=12, minute=0, tzinfo=timezone.utc)
    OVERLAP_START = time(hour=13, minute=0, tzinfo=timezone.utc)
    OVERLAP_END = time(hour=17, minute=0, tzinfo=timezone.utc)

    def __init__(self, now_provider: Optional[Callable[[], datetime]] = None) -> None:
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._started = False
        self._shutdown_event = asyncio.Event()
        self._current_session: SessionState = SessionState.OFF
        self._session_callbacks: list[Callable[[SessionState, SessionState], None]] = []
        self._monitor_task: Optional[asyncio.Task[None]] = None

    async def startup(self) -> None:
        """Start the session scheduler."""
        if self._started:
            return

        self._started = True
        self._shutdown_event.clear()

        # Initialize current session state
        self._update_current_session()

        # Start monitoring task
        self._monitor_task = asyncio.create_task(self._monitor_sessions())

        logging.info("Session scheduler started")

    async def shutdown(self) -> None:
        """Shutdown the session scheduler."""
        if not self._started:
            return

        self._started = False
        self._shutdown_event.set()

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        logging.info("Session scheduler shut down")

    def add_session_callback(self, callback: Callable[[SessionState, SessionState], None]) -> None:
        """Add a callback for session state changes."""
        self._session_callbacks.append(callback)

    def get_current_session(self) -> SessionState:
        """Get the current trading session."""
        self._update_current_session()
        return self._current_session

    def is_active_session(self) -> bool:
        """Check if we're currently in an active trading session."""
        current = self.get_current_session()
        return current in {SessionState.LONDON, SessionState.OVERLAP}

    def _update_current_session(self) -> None:
        """Update the current session state based on current time."""
        now = self._now_provider()
        current_time = now.time()
        previous_session = self._current_session

        # Determine session based on time windows
        if self.LONDON_START <= current_time < self.LONDON_END:
            self._current_session = SessionState.LONDON
        elif self.OVERLAP_START <= current_time < self.OVERLAP_END:
            self._current_session = SessionState.OVERLAP
        else:
            self._current_session = SessionState.OFF

        # Notify callbacks if session changed
        if previous_session != self._current_session:
            self._notify_session_change(previous_session, self._current_session)

    def _notify_session_change(self, old_session: SessionState, new_session: SessionState) -> None:
        """Notify all callbacks of a session state change."""
        logging.info(
            "Session state changed: %s -> %s at %s",
            old_session.value,
            new_session.value,
            self._now_provider().isoformat(),
        )

        for callback in self._session_callbacks:
            try:
                callback(old_session, new_session)
            except Exception as exc:
                logging.exception("Error in session callback: %s", exc)

    async def _monitor_sessions(self) -> None:
        """Monitor session state changes in the background."""
        while self._started and not self._shutdown_event.is_set():
            try:
                self._update_current_session()
                await asyncio.sleep(10)  # Check every 10 seconds
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logging.exception("Error in session monitoring: %s", exc)
                await asyncio.sleep(30)  # Back off on error

    def get_session_info(self) -> dict:
        """Get detailed session information."""
        now = self._now_provider()
        current_time = now.time()

        # Calculate time to next session change
        time_to_change = None
        if self._current_session == SessionState.OFF:
            # Find next session start
            if current_time < self.LONDON_START:
                time_to_change = datetime.combine(
                    now.date(), self.LONDON_START, timezone.utc
                ) - now
            elif current_time < self.OVERLAP_START:
                time_to_change = datetime.combine(
                    now.date(), self.OVERLAP_START, timezone.utc
                ) - now
            else:
                # Next day's London session
                tomorrow = now.date() + datetime.timedelta(days=1)
                time_to_change = datetime.combine(
                    tomorrow, self.LONDON_START, timezone.utc
                ) - now
        elif self._current_session == SessionState.LONDON:
            # Time to overlap start
            change_time = datetime.combine(now.date(), self.OVERLAP_START, timezone.utc)
            time_to_change = change_time - now
        elif self._current_session == SessionState.OVERLAP:
            # Time to session end
            change_time = datetime.combine(now.date(), self.OVERLAP_END, timezone.utc)
            time_to_change = change_time - now

        return {
            "current_session": self._current_session.value,
            "current_time": now.isoformat(),
            "is_active": self.is_active_session(),
            "time_to_change_seconds": time_to_change.total_seconds() if time_to_change else None,
            "london_window": {
                "start": self.LONDON_START.isoformat(),
                "end": self.LONDON_END.isoformat(),
                "duration_minutes": 240,  # 4 hours
            },
            "overlap_window": {
                "start": self.OVERLAP_START.isoformat(),
                "end": self.OVERLAP_END.isoformat(),
                "duration_minutes": 240,  # 4 hours
            },
        }