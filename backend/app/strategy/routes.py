"""FastAPI routes for strategy framework."""
from __future__ import annotations

from fastapi import APIRouter

from .analyzers.context import get_context_analyzer
from .engine import get_strategy_engine

router = APIRouter()


@router.get("/strategy/status")
async def strategy_status():
    """Get the current status of the strategy engine and components."""
    engine = get_strategy_engine()
    analyzer = get_context_analyzer()

    # Get engine state
    engine_state = engine.get_state()

    # Get context analysis
    context_analysis = analyzer.analyze()

    # Get scheduler state
    scheduler_state = engine.scheduler.get_session_info()

    return {
        "engine_state": engine_state.model_dump(),
        "context_analysis": context_analysis.model_dump() if context_analysis else None,
        "scheduler_state": scheduler_state,
    }


@router.get("/strategy/candles")
async def get_candles(timeframe: str = "1m", count: int = 100):
    """Get recent candles for a specific timeframe."""
    from .models import Timeframe

    engine = get_strategy_engine()

    try:
        tf = Timeframe(timeframe)
    except ValueError:
        return {"error": f"Invalid timeframe: {timeframe}. Use 1m or 5m"}

    candles = engine.get_candles(tf, count)
    return {
        "timeframe": timeframe,
        "count": len(candles),
        "candles": [candle.model_dump() for candle in candles],
    }


@router.get("/strategy/analysis/diagnostics")
async def get_analysis_diagnostics():
    """Get detailed diagnostics from the context analyzer."""
    analyzer = get_context_analyzer()
    return analyzer.get_diagnostics()