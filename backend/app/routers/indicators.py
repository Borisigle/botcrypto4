"""Indicators router exposing endpoints for technical metrics such as CVD."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.indicators import CVDSnapshot
from app.services.cvd_service import get_cvd_service
from app.services.trade_service import TradeService
from app.ws.routes import get_ws_module

router = APIRouter(prefix="/indicators", tags=["indicators"])


def get_trade_service() -> TradeService:
    ws_module = get_ws_module()
    return ws_module.trade_service


@router.get("/cvd", response_model=CVDSnapshot)
async def get_cvd(
    trade_service: TradeService = Depends(get_trade_service)
) -> CVDSnapshot:
    """Return the current CVD snapshot since the last reset."""
    try:
        cvd_service = get_cvd_service()
        trades = trade_service.get_recent_trades(limit=999_999)
        return cvd_service.build_snapshot(trades, record_history=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/cvd/history", response_model=List[CVDSnapshot])
async def get_cvd_history(
    limit: int = Query(default=100, ge=1, le=1000)
) -> List[CVDSnapshot]:
    """Return the latest CVD history snapshots (up to the specified limit)."""
    try:
        cvd_service = get_cvd_service()
        return cvd_service.get_history(limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/cvd/reset", response_model=CVDSnapshot)
async def reset_cvd(
    trade_service: TradeService = Depends(get_trade_service)
) -> CVDSnapshot:
    """Reset the CVD period manually and return the new baseline snapshot."""
    try:
        cvd_service = get_cvd_service()
        cvd_service.reset_cvd(reason="manual")
        trades = trade_service.get_recent_trades(limit=999_999)
        return cvd_service.build_snapshot(trades, record_history=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
