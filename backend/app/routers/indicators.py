"""Indicators router exposing endpoints for technical metrics such as CVD."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.indicators import CVDSnapshot, VolumeDeltaSnapshot
from app.services.cvd_service import get_cvd_service
from app.services.trade_service import TradeService
from app.services.volume_delta_service import get_volume_delta_service
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


@router.get("/volume-delta", response_model=VolumeDeltaSnapshot)
async def get_volume_delta(
    period: int = Query(default=60, ge=1, le=3600),
    trade_service: TradeService = Depends(get_trade_service)
) -> VolumeDeltaSnapshot:
    """GET /indicators/volume-delta?period=60 - Volume delta para período"""
    try:
        volume_delta_service = get_volume_delta_service()
        trades = trade_service.get_recent_trades(limit=999_999)
        delta_data = volume_delta_service.calculate_volume_delta(trades, period)
        snapshot = volume_delta_service.record_snapshot(delta_data)
        return snapshot
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/volume-delta/history")
async def get_volume_delta_history(
    period: int = Query(default=60, ge=1, le=3600),
    limit: int = Query(default=100, ge=1, le=1000)
) -> List[VolumeDeltaSnapshot]:
    """GET /indicators/volume-delta/history?period=60&limit=100"""
    try:
        volume_delta_service = get_volume_delta_service()
        return volume_delta_service.get_history(period=period, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/volume-delta/multi")
async def get_volume_delta_multi(
    trade_service: TradeService = Depends(get_trade_service)
) -> dict:
    """GET /indicators/volume-delta/multi - Volume delta para múltiples períodos"""
    try:
        volume_delta_service = get_volume_delta_service()
        trades = trade_service.get_recent_trades(limit=999_999)
        return {
            "1m": volume_delta_service.calculate_volume_delta(trades, 60),
            "5m": volume_delta_service.calculate_volume_delta(trades, 300),
            "15m": volume_delta_service.calculate_volume_delta(trades, 900),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
