"""Data models for the strategy framework."""
from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Timeframe(str, Enum):
    """Candle timeframes supported by the strategy engine."""
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"


class SessionState(str, Enum):
    """Trading session states."""
    OFF = "off"
    LONDON = "london"
    OVERLAP = "overlap"


class MarketRegime(str, Enum):
    """Market regime classifications."""
    RANGE = "range"
    TREND = "trend"


class Candle(BaseModel):
    """OHLCV candle data."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: Timeframe
    trades: int = 0


class StrategyEngineState(BaseModel):
    """Current state of the strategy engine."""
    is_running: bool
    current_session: SessionState
    active_timeframes: List[Timeframe]
    last_update: datetime
    candle_buffers: Dict[str, List[Candle]]


class ContextAnalysis(BaseModel):
    """Analysis results from the context analyzer."""
    regime: MarketRegime
    confidence: float = Field(ge=0.0, le=1.0)
    vwap: Optional[float] = None
    poc: Optional[float] = None
    cumulative_delta: Optional[float] = None
    volume_profile_strength: Optional[float] = None
    timestamp: datetime


class StrategyStatus(BaseModel):
    """Combined status response for the strategy endpoint."""
    engine_state: StrategyEngineState
    context_analysis: Optional[ContextAnalysis] = None
    scheduler_state: Dict[str, Any]


class TradeSignal(BaseModel):
    """Trading signal emitted by strategy components."""
    timestamp: datetime
    symbol: str
    signal_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    price: Optional[float] = None
    quantity: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StrategyEvent(BaseModel):
    """Generic event for strategy component communication."""
    timestamp: datetime
    event_type: str
    source: str
    data: Dict[str, Any] = Field(default_factory=dict)