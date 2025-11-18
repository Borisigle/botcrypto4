"""Trade router for exposing trade data via REST API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from ..services.trade_service import TradeService
from ..ws.routes import get_ws_module

router = APIRouter(prefix="/trades", tags=["trades"])


def get_trade_service() -> TradeService:
    """Dependency to get trade service instance from WSModule."""
    ws_module = get_ws_module()
    return ws_module.trade_service


@router.get("")
async def get_trades(
    limit: int = Query(default=100, le=1000, ge=1),
    service: TradeService = Depends(get_trade_service)
) -> List[dict]:
    """Get most recent trades."""
    try:
        return service.get_recent_trades(limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats")
async def get_trade_stats(
    service: TradeService = Depends(get_trade_service)
) -> dict:
    """Get trade statistics."""
    try:
        return service.get_stats()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/range")
async def get_trades_range(
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    service: TradeService = Depends(get_trade_service)
) -> List[dict]:
    """Get trades within time range."""
    try:
        if start_time >= end_time:
            raise HTTPException(
                status_code=400, 
                detail="start_time must be before end_time"
            )
        return service.get_trades_range(start_time, end_time)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))