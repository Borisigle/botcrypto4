"""Tests for the strategy engine and components."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.strategy.analyzers.context import ContextAnalyzer
from app.strategy.engine import StrategyEngine
from app.strategy.models import (
    Candle,
    ContextAnalysis,
    MarketRegime,
    SessionState,
    StrategyEngineState,
    Timeframe,
)
from app.strategy.scheduler import SessionScheduler
from app.ws.models import TradeTick, TradeSide


class TestSessionScheduler:
    """Test the session scheduler component."""

    @pytest.fixture
    def scheduler(self):
        """Create a session scheduler instance."""
        now = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)  # 10:00 UTC
        scheduler = SessionScheduler(now_provider=lambda: now)
        return scheduler

    @pytest.mark.asyncio
    async def test_startup_shutdown(self, scheduler):
        """Test scheduler startup and shutdown."""
        await scheduler.startup()
        assert scheduler._started

        await scheduler.shutdown()
        assert not scheduler._started

    def test_london_session(self):
        """Test London session detection."""
        # Test during London session (10:00 UTC)
        now = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        session = scheduler.get_current_session()
        assert session == SessionState.LONDON
        assert scheduler.is_active_session()

    def test_overlap_session(self):
        """Test overlap session detection."""
        # Test during overlap session (14:00 UTC)
        now = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        session = scheduler.get_current_session()
        assert session == SessionState.OVERLAP
        assert scheduler.is_active_session()

    def test_off_session(self):
        """Test off-session detection."""
        # Test during off session (18:00 UTC)
        now = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        session = scheduler.get_current_session()
        assert session == SessionState.WAITING_FOR_SESSION
        assert not scheduler.is_active_session()

    def test_session_boundaries(self):
        """Test session boundary conditions."""
        # Test exact boundary times
        london_start = datetime(2024, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
        london_end = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        overlap_start = datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        overlap_end = datetime(2024, 1, 15, 17, 0, 0, tzinfo=timezone.utc)

        # London start should be active
        scheduler = SessionScheduler(now_provider=lambda: london_start)
        assert scheduler.get_current_session() == SessionState.LONDON

        # London end should be waiting for session
        scheduler = SessionScheduler(now_provider=lambda: london_end)
        assert scheduler.get_current_session() == SessionState.WAITING_FOR_SESSION

        # Overlap start should be active
        scheduler = SessionScheduler(now_provider=lambda: overlap_start)
        assert scheduler.get_current_session() == SessionState.OVERLAP

        # Overlap end should be waiting for session
        scheduler = SessionScheduler(now_provider=lambda: overlap_end)
        assert scheduler.get_current_session() == SessionState.WAITING_FOR_SESSION

    def test_session_callbacks(self):
        """Test session change callbacks."""
        callback_calls = []
        
        def test_callback(old_session, new_session):
            callback_calls.append((old_session, new_session))

        # Start in off session
        now = datetime(2024, 1, 15, 7, 59, 59, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        scheduler.add_session_callback(test_callback)

        # Advance to London session
        now = datetime(2024, 1, 15, 8, 0, 1, tzinfo=timezone.utc)
        scheduler._now_provider = lambda: now
        scheduler._update_current_session()

        assert len(callback_calls) == 1
        assert callback_calls[0] == (SessionState.OFF, SessionState.LONDON)

    def test_session_info(self):
        """Test session info output."""
        now = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        scheduler = SessionScheduler(now_provider=lambda: now)
        
        info = scheduler.get_session_info()
        
        assert info["current_session"] == SessionState.LONDON.value
        assert info["is_active"] is True
        assert "time_to_change_seconds" in info
        assert "london_window" in info
        assert "overlap_window" in info


class TestStrategyEngine:
    """Test the strategy engine component."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.symbol = "BTCUSDT"
        return settings

    @pytest.fixture
    def mock_context_service(self):
        """Create mock context service."""
        service = MagicMock()
        return service

    @pytest.fixture
    def mock_ws_module(self):
        """Create mock WS module."""
        module = MagicMock()
        return module

    @pytest.fixture
    def engine(self, mock_settings, mock_context_service, mock_ws_module):
        """Create a strategy engine instance."""
        return StrategyEngine(
            settings=mock_settings,
            context_service=mock_context_service,
            ws_module=mock_ws_module,
        )

    def test_engine_initialization(self, engine):
        """Test engine initialization."""
        assert not engine._started
        assert engine._active_timeframes == [Timeframe.ONE_MINUTE, Timeframe.FIVE_MINUTES]
        assert Timeframe.ONE_MINUTE in engine._candle_buffers
        assert Timeframe.FIVE_MINUTES in engine._candle_buffers

    @pytest.mark.asyncio
    async def test_engine_startup_shutdown(self, engine):
        """Test engine startup and shutdown."""
        await engine.startup()
        assert engine._started
        assert engine.scheduler._started

        await engine.shutdown()
        assert not engine._started
        assert not engine.scheduler._started

    def test_ingest_trade_active_session(self, engine):
        """Test trade ingestion during active session."""
        # Set time to London session (10:00 UTC)
        now = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        engine._now_provider = lambda: now
        engine.scheduler._now_provider = lambda: now
        
        # Create test trade
        trade = TradeTick(
            ts=now,
            price=100.0,
            qty=1.0,
            side=TradeSide.BUY,
            isBuyerMaker=False,
            id=1,
        )

        # Initialize candle buffer
        engine._initialize_candle_buffers()
        for timeframe in engine._active_timeframes:
            engine._candle_buffers[timeframe]["start_time"] = engine._align_to_timeframe(now, timeframe)

        # Ingest trade
        engine.ingest_trade(trade)

        # Check that OHLCV was updated
        for timeframe in engine._active_timeframes:
            buffer_data = engine._candle_buffers[timeframe]
            assert buffer_data["open"] == 100.0
            assert buffer_data["high"] == 100.0
            assert buffer_data["low"] == 100.0
            assert buffer_data["close"] == 100.0
            assert buffer_data["volume"] == 1.0
            assert buffer_data["trades"] == 1

    def test_ingest_trade_inactive_session(self, engine):
        """Test trade ingestion during inactive session."""
        # Mock inactive session (WAITING_FOR_SESSION)
        engine.scheduler._current_session = SessionState.WAITING_FOR_SESSION
        
        # Create test trade
        trade = TradeTick(
            ts=datetime.now(timezone.utc),
            price=100.0,
            qty=1.0,
            side=TradeSide.BUY,
            isBuyerMaker=False,
            id=1,
        )

        # Initialize candle buffer
        now = datetime.now(timezone.utc)
        engine._initialize_candle_buffers()
        for timeframe in engine._active_timeframes:
            engine._candle_buffers[timeframe]["start_time"] = engine._align_to_timeframe(now, timeframe)

        # Ingest trade (should be ignored)
        engine.ingest_trade(trade)

        # Check that OHLCV was not updated
        for timeframe in engine._active_timeframes:
            buffer_data = engine._candle_buffers[timeframe]
            assert buffer_data["open"] is None
            assert buffer_data["volume"] == 0.0
            assert buffer_data["trades"] == 0

    def test_candle_aggregation(self, engine):
        """Test candle aggregation logic."""
        # Initialize candle buffer during London session
        now = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        engine._now_provider = lambda: now
        engine._initialize_candle_buffers()
        
        for timeframe in engine._active_timeframes:
            buffer_data = engine._candle_buffers[timeframe]
            buffer_data["start_time"] = engine._align_to_timeframe(now, timeframe)
            buffer_data["open"] = 100.0
            buffer_data["high"] = 100.0
            buffer_data["low"] = 100.0
            buffer_data["close"] = 100.0
            buffer_data["volume"] = 1.0
            buffer_data["trades"] = 1

        # Simulate time advance to complete candles (need 6 minutes for 5m candle)
        future_time = now + timedelta(minutes=6)
        engine._now_provider = lambda: future_time

        # Run aggregation
        asyncio.run(engine._aggregate_candles())

        # Check that candle was completed
        for timeframe in engine._active_timeframes:
            buffer_data = engine._candle_buffers[timeframe]
            assert len(buffer_data["buffer"]) > 0
            candle = buffer_data["buffer"][-1]
            assert isinstance(candle, Candle)
            assert candle.open == 100.0
            assert candle.volume == 1.0
            assert candle.trades == 1

    def test_get_state(self, engine):
        """Test getting engine state."""
        state = engine.get_state()
        
        assert isinstance(state, StrategyEngineState)
        assert not state.is_running
        # Current session is WAITING_FOR_SESSION when outside trading hours
        assert state.current_session in [SessionState.OFF, SessionState.WAITING_FOR_SESSION]
        assert Timeframe.ONE_MINUTE in state.active_timeframes
        assert Timeframe.FIVE_MINUTES in state.active_timeframes
        assert "1m" in state.candle_buffers
        assert "5m" in state.candle_buffers

    def test_get_candles(self, engine):
        """Test getting candles."""
        # Add some test candles
        now = datetime.now(timezone.utc)
        candle = Candle(
            timestamp=now,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            timeframe=Timeframe.ONE_MINUTE,
            trades=5,
        )
        
        engine._candle_buffers[Timeframe.ONE_MINUTE]["buffer"].append(candle)
        
        candles = engine.get_candles(Timeframe.ONE_MINUTE, 10)
        assert len(candles) == 1
        assert candles[0].open == 100.0

    def test_timeframe_alignment(self, engine):
        """Test timeframe alignment logic."""
        # Test 1-minute alignment
        timestamp = datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc)
        aligned = engine._align_to_timeframe(timestamp, Timeframe.ONE_MINUTE)
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert aligned == expected

        # Test 5-minute alignment
        timestamp = datetime(2024, 1, 15, 10, 32, 45, tzinfo=timezone.utc)
        aligned = engine._align_to_timeframe(timestamp, Timeframe.FIVE_MINUTES)
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert aligned == expected

    def test_event_system(self, engine):
        """Test event subscription and emission."""
        events = []
        
        def event_handler(event):
            events.append(event)

        engine.subscribe_events("test_event", event_handler)
        engine._emit_event("test_event", {"test": "data"})

        assert len(events) == 1
        assert events[0].event_type == "test_event"
        assert events[0].data["test"] == "data"


class TestContextAnalyzer:
    """Test the context analyzer component."""

    @pytest.fixture
    def mock_context_service(self):
        """Create mock context service."""
        service = MagicMock()
        
        # Mock context payload
        service.context_payload.return_value = {
            "session": {"state": "london"},
        }
        
        # Mock levels payload
        service.levels_payload.return_value = {
            "VWAP": 100.0,
            "POCd": 99.5,
        }
        
        # Mock stats payload
        service.stats_payload.return_value = {
            "cd_pre": 50.0,
        }
        
        # Mock debug POC payload
        service.debug_poc_payload.return_value = {
            "top_bins": [
                {"price": 99.5, "volume": 100.0},
                {"price": 100.0, "volume": 80.0},
                {"price": 99.0, "volume": 60.0},
            ],
        }
        
        return service

    @pytest.fixture
    def analyzer(self, mock_context_service):
        """Create a context analyzer instance."""
        return ContextAnalyzer(context_service=mock_context_service)

    def test_analyze_success(self, analyzer):
        """Test successful analysis."""
        analysis = analyzer.analyze()
        
        assert isinstance(analysis, ContextAnalysis)
        assert analysis.regime in [MarketRegime.RANGE, MarketRegime.TREND]
        assert 0.0 <= analysis.confidence <= 1.0
        assert analysis.vwap == 100.0
        assert analysis.poc == 99.5
        assert analysis.cumulative_delta == 50.0

    def test_analyze_insufficient_volume(self, analyzer):
        """Test analysis with insufficient volume."""
        # Mock low volume
        analyzer.context_service.debug_poc_payload.return_value = {
            "top_bins": [{"price": 99.5, "volume": 10.0}],
        }
        
        analysis = analyzer.analyze()
        
        assert analysis.regime == MarketRegime.RANGE
        assert analysis.confidence == 0.3  # Low confidence due to insufficient volume

    def test_analyze_error_handling(self, analyzer):
        """Test error handling in analysis."""
        # Mock service to raise exception
        analyzer.context_service.context_payload.side_effect = Exception("Test error")
        
        analysis = analyzer.analyze()
        
        assert analysis is None

    def test_regime_classification_range(self, analyzer):
        """Test range regime classification."""
        # Mock range-like conditions
        analyzer.context_service.levels_payload.return_value = {
            "VWAP": 100.0,
            "POCd": 100.1,  # Close to VWAP
        }
        
        analyzer.context_service.stats_payload.return_value = {
            "cd_pre": 5.0,  # Low delta
        }
        
        analyzer.context_service.debug_poc_payload.return_value = {
            "top_bins": [
                {"price": 100.0, "volume": 150.0},  # High concentration
                {"price": 100.1, "volume": 100.0},
                {"price": 99.9, "volume": 80.0},
            ],
        }
        
        analysis = analyzer.analyze()
        
        # Should classify as range due to high volume concentration and low delta
        assert analysis.regime == MarketRegime.RANGE

    def test_regime_classification_trend(self, analyzer):
        """Test trend regime classification."""
        # Mock trend-like conditions
        analyzer.context_service.levels_payload.return_value = {
            "VWAP": 100.0,
            "POCd": 95.0,  # Far from VWAP
        }
        
        analyzer.context_service.stats_payload.return_value = {
            "cd_pre": 200.0,  # High delta
        }
        
        analyzer.context_service.debug_poc_payload.return_value = {
            "top_bins": [
                {"price": 95.0, "volume": 50.0},  # Low concentration
                {"price": 96.0, "volume": 45.0},
                {"price": 97.0, "volume": 40.0},
            ],
        }
        
        analysis = analyzer.analyze()
        
        # Should classify as trend due to high delta and dispersed volume
        assert analysis.regime == MarketRegime.TREND

    def test_volume_profile_strength(self, analyzer):
        """Test volume profile strength calculation."""
        # Test with concentrated volume
        analyzer.context_service.debug_poc_payload.return_value = {
            "top_bins": [
                {"price": 100.0, "volume": 200.0},
                {"price": 100.1, "volume": 50.0},
                {"price": 99.9, "volume": 30.0},
            ],
        }
        
        analysis = analyzer.analyze()
        
        # Should have high strength due to concentrated volume
        assert analysis.volume_profile_strength is not None
        assert 0.0 <= analysis.volume_profile_strength <= 1.0

    def test_get_diagnostics(self, analyzer):
        """Test getting diagnostics."""
        diagnostics = analyzer.get_diagnostics()
        
        assert "analysis" in diagnostics
        assert "context" in diagnostics
        assert "levels" in diagnostics
        assert "volume_profile" in diagnostics
        assert "parameters" in diagnostics

    def test_get_diagnostics_error(self, analyzer):
        """Test diagnostics error handling."""
        # Mock service to raise exception
        analyzer.context_service.context_payload.side_effect = Exception("Test error")
        
        diagnostics = analyzer.get_diagnostics()
        
        assert "error" in diagnostics


class TestIntegration:
    """Integration tests for strategy components."""

    @pytest.mark.asyncio
    async def test_strategy_engine_with_real_scheduler(self):
        """Test strategy engine with real scheduler integration."""
        engine = StrategyEngine()
        
        await engine.startup()
        
        # Test that scheduler is properly integrated
        assert engine.scheduler._started
        
        # Test session state propagation
        state = engine.get_state()
        assert isinstance(state.current_session, SessionState)
        
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_candle_aggregation_integration(self):
        """Test full candle aggregation workflow."""
        engine = StrategyEngine()
        
        # Mock active session
        engine.scheduler._current_session = SessionState.LONDON
        
        await engine.startup()
        
        # Create test trades
        now = datetime.now(timezone.utc)
        trades = [
            TradeTick(
                ts=now + timedelta(seconds=i),
                price=100.0 + i * 0.1,
                qty=1.0,
                side=TradeSide.BUY,
                isBuyerMaker=False,
                id=i,
            )
            for i in range(10)
        ]
        
        # Ingest trades
        for trade in trades:
            engine.ingest_trade(trade)
        
        # Wait a bit for aggregation
        await asyncio.sleep(0.1)
        
        # Check that candles are being built
        state = engine.get_state()
        assert state.candle_buffers
        
        await engine.shutdown()

    def test_context_analyzer_deterministic_classification(self):
        """Test that context analyzer produces deterministic results."""
        # Create a deterministic mock context service
        context_service = MagicMock()
        context_service.context_payload.return_value = {
            "session": {"state": "london"},
        }
        context_service.levels_payload.return_value = {
            "VWAP": 100.0,
            "POCd": 100.05,
        }
        context_service.stats_payload.return_value = {
            "cd_pre": 10.0,
        }
        context_service.debug_poc_payload.return_value = {
            "top_bins": [
                {"price": 100.0, "volume": 100.0},
                {"price": 100.05, "volume": 80.0},
                {"price": 99.95, "volume": 60.0},
            ],
        }
        
        analyzer = ContextAnalyzer(context_service=context_service)
        
        # Run analysis multiple times
        results = []
        for _ in range(10):
            analysis = analyzer.analyze()
            results.append((analysis.regime, analysis.confidence))
        
        # All results should be identical
        assert all(result == results[0] for result in results)
        
        # Should classify as range due to close VWAP/POC and concentrated volume
        regime, confidence = results[0]
        assert regime == MarketRegime.RANGE
        assert confidence > 0.5