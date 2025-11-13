"""FastAPI routes for strategy framework."""
from __future__ import annotations

from fastapi import APIRouter

from .analyzers.context import get_context_analyzer
from .analyzers.orderflow import get_orderflow_analyzer
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


@router.get("/strategy/metrics")
async def get_metrics():
    """Get latest market metrics from order flow analyzer.

    Returns metrics including:
    - vwap: Volume-weighted average price
    - poc: Point of control
    - delta: Cumulative delta (buy_vol - sell_vol)
    - buy_volume: Total buy volume
    - sell_volume: Total sell volume
    - footprint: Top 20 price bins by volume
    - trade_count: Number of trades processed
    - backfill_complete: Whether backfill is complete
    - metrics_precision: PRECISE or IMPRECISE with percentage
    """
    from app.context.service import get_context_service
    
    analyzer = get_orderflow_analyzer()
    metrics_data = analyzer.get_metrics_with_metadata()
    
    # Add backfill status information
    context_service = get_context_service()
    backfill_complete = context_service.backfill_complete
    backfill_progress = context_service.backfill_progress
    
    # Determine metrics precision
    if not backfill_complete:
        status = backfill_progress.get("status", "idle")
        percentage = backfill_progress.get("percentage", 0.0)
        current = backfill_progress.get("current", 0)
        total = backfill_progress.get("total", 0)
        
        if status == "in_progress":
            metrics_precision = f"IMPRECISE (backfill {percentage:.0f}%)"
            warning = f"Metrics based on partial data ({current}/{total} chunks loaded)"
        else:
            metrics_precision = "IMPRECISE (backfill pending)"
            warning = "Backfill not yet started"
    else:
        metrics_precision = "PRECISE"
        warning = None
    
    # Add backfill information to response
    metrics_data["backfill_complete"] = backfill_complete
    metrics_data["metrics_precision"] = metrics_precision
    if warning:
        if metrics_data.get("metrics"):
            metrics_data["metrics"]["warning"] = warning
    
    return metrics_data