"""Tests for the session scheduler with timezone-aware comparisons."""
import asyncio
from datetime import datetime, time, timedelta, timezone

import pytest

from app.strategy.models import SessionState
from app.strategy.scheduler import SessionScheduler


class TestSessionSchedulerTimezoneAware:
    """Tests for timezone-aware session scheduling."""

    def test_london_session_detection(self):
        """Test detection of London session (08:00-12:00 UTC)."""
        # 09:00 UTC
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        assert scheduler.get_current_session() == SessionState.LONDON

    def test_overlap_session_detection(self):
        """Test detection of overlap session (13:00-17:00 UTC)."""
        # 15:00 UTC
        now = datetime(2024, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        assert scheduler.get_current_session() == SessionState.OVERLAP

    def test_waiting_for_session_outside_hours(self):
        """Test WAITING_FOR_SESSION state when outside trading hours."""
        # 06:00 UTC - before London session
        now = datetime(2024, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        assert scheduler.get_current_session() == SessionState.WAITING_FOR_SESSION

    def test_waiting_for_session_between_london_and_overlap(self):
        """Test WAITING_FOR_SESSION state between London and overlap."""
        # 12:30 UTC - between London and overlap
        now = datetime(2024, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        assert scheduler.get_current_session() == SessionState.WAITING_FOR_SESSION

    def test_waiting_for_session_after_overlap(self):
        """Test WAITING_FOR_SESSION state after overlap ends."""
        # 18:00 UTC - after overlap session ends
        now = datetime(2024, 1, 1, 18, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        assert scheduler.get_current_session() == SessionState.WAITING_FOR_SESSION

    def test_is_active_session_excludes_waiting_state(self):
        """Test that is_active_session() returns False for WAITING_FOR_SESSION."""
        # 06:00 UTC - waiting state
        now = datetime(2024, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        assert not scheduler.is_active_session()

    def test_is_active_session_includes_london(self):
        """Test that is_active_session() returns True during London."""
        # 09:00 UTC - London session
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        assert scheduler.is_active_session()

    def test_is_active_session_includes_overlap(self):
        """Test that is_active_session() returns True during overlap."""
        # 15:00 UTC - overlap session
        now = datetime(2024, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        assert scheduler.is_active_session()

    def test_timezone_aware_datetime_handling(self):
        """Test handling of timezone-aware datetimes from different timezones."""
        # Create a datetime in EST (-5 hours)
        est = timezone(timedelta(hours=-5))
        now_est = datetime(2024, 1, 1, 4, 0, 0, tzinfo=est)  # 04:00 EST = 09:00 UTC
        
        scheduler = SessionScheduler(now_provider=lambda: now_est)
        scheduler._update_current_session()
        
        # Should be detected as London session (09:00 UTC)
        assert scheduler.get_current_session() == SessionState.LONDON

    def test_session_boundaries_start_of_london(self):
        """Test exact boundary at start of London session."""
        # 08:00:00 UTC - exact start of London session
        now = datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        assert scheduler.get_current_session() == SessionState.LONDON

    def test_session_boundaries_end_of_london(self):
        """Test exact boundary at end of London session."""
        # 12:00:00 UTC - exact end of London session (not included)
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        assert scheduler.get_current_session() == SessionState.WAITING_FOR_SESSION

    def test_session_boundaries_start_of_overlap(self):
        """Test exact boundary at start of overlap session."""
        # 13:00:00 UTC - exact start of overlap session
        now = datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        assert scheduler.get_current_session() == SessionState.OVERLAP

    def test_session_boundaries_end_of_overlap(self):
        """Test exact boundary at end of overlap session."""
        # 17:00:00 UTC - exact end of overlap session (not included)
        now = datetime(2024, 1, 1, 17, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        assert scheduler.get_current_session() == SessionState.WAITING_FOR_SESSION

    def test_time_to_change_from_waiting_to_london(self):
        """Test time calculation to next London session from waiting state."""
        # 06:00 UTC - 2 hours before London
        now = datetime(2024, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        session_info = scheduler.get_session_info()
        # Should be ~2 hours (7200 seconds)
        assert session_info["time_to_change_seconds"] == 7200

    def test_time_to_change_from_london_to_overlap(self):
        """Test time calculation to overlap from London session."""
        # 10:00 UTC - 3 hours before overlap
        now = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        session_info = scheduler.get_session_info()
        # Should be ~3 hours (10800 seconds)
        assert session_info["time_to_change_seconds"] == 10800

    def test_time_to_change_from_overlap_to_end(self):
        """Test time calculation from overlap to end of session."""
        # 15:00 UTC - 2 hours before end
        now = datetime(2024, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler._update_current_session()
        
        session_info = scheduler.get_session_info()
        # Should be ~2 hours (7200 seconds)
        assert session_info["time_to_change_seconds"] == 7200

    def test_session_info_contains_required_fields(self):
        """Test that session info contains all required fields."""
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        session_info = scheduler.get_session_info()
        
        assert "current_session" in session_info
        assert "current_time" in session_info
        assert "is_active" in session_info
        assert "time_to_change_seconds" in session_info
        assert "london_window" in session_info
        assert "overlap_window" in session_info

    @pytest.mark.asyncio
    async def test_startup_initializes_session_state(self):
        """Test that startup initializes the session state."""
        now = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        await scheduler.startup()
        
        assert scheduler.get_current_session() == SessionState.LONDON
        assert scheduler.is_active_session()
        
        await scheduler.shutdown()

    @pytest.mark.asyncio
    async def test_session_change_callback(self):
        """Test that session change callbacks are triggered."""
        now = datetime(2024, 1, 1, 7, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        callback_called = []
        
        def callback(old_session, new_session):
            callback_called.append((old_session, new_session))
        
        # First update to initialize state
        scheduler._update_current_session()
        
        # Now add callback - at this point we're in WAITING_FOR_SESSION
        scheduler.add_session_callback(callback)
        
        # Initial state should be WAITING_FOR_SESSION
        assert scheduler.get_current_session() == SessionState.WAITING_FOR_SESSION
        assert len(callback_called) == 0  # No callbacks yet
        
        # Simulate time change to London session
        now_london = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        scheduler._now_provider = lambda: now_london
        scheduler._update_current_session()
        
        # Callback should have been called
        assert len(callback_called) == 1
        assert callback_called[0] == (SessionState.WAITING_FOR_SESSION, SessionState.LONDON)
