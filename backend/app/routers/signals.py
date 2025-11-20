"""Router for trading signals and sweep detection."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.indicators import Signal
from app.services.cvd_service import get_cvd_service
from app.services.liquidation_service import get_liquidation_service
from app.services.sweep_detector import get_sweep_detector
from app.services.trade_service import TradeService
from app.services.volume_delta_service import get_volume_delta_service
from app.ws.routes import get_ws_module

router = APIRouter(prefix="/signals", tags=["signals"])
logger = logging.getLogger(__name__)


def get_trade_service() -> TradeService:
    ws_module = get_ws_module()
    return ws_module.trade_service


@router.get("/current", response_model=Optional[Signal])
async def get_current_signal() -> Optional[Signal]:
    """GET /signals/current - Return the last generated signal."""
    try:
        sweep_detector = get_sweep_detector()
        return sweep_detector.get_last_signal()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/history", response_model=List[Signal])
async def get_signals_history(
    limit: int = Query(default=50, ge=1, le=100)
) -> List[Signal]:
    """GET /signals/history?limit=50 - Return signal history."""
    try:
        sweep_detector = get_sweep_detector()
        return sweep_detector.get_signals_history(limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/analyze", response_model=Optional[Signal])
async def analyze_setup(
    trade_service: TradeService = Depends(get_trade_service),
) -> Optional[Signal]:
    """POST /signals/analyze - Analyze current setup and generate signal."""
    try:
        # Get current data
        trades = trade_service.get_recent_trades(limit=999_999)
        if not trades:
            raise ValueError("No trades available for analysis")

        current_price = trades[-1].get("price") if trades else 0
        if current_price <= 0:
            raise ValueError("Invalid current price")

        # Get indicators
        cvd_service = get_cvd_service()
        cvd_snapshot = cvd_service.build_snapshot(trades, record_history=False)
        cvd_snapshot_dict = cvd_snapshot.dict()

        volume_delta_service = get_volume_delta_service()
        vol_delta_snapshot = volume_delta_service.calculate_volume_delta(trades, 60)

        # Get liquidation levels
        liquidation_service = get_liquidation_service()
        support = liquidation_service.get_nearest_support(current_price)
        resistance = liquidation_service.get_nearest_resistance(current_price)

        # Analyze
        sweep_detector = get_sweep_detector()
        signal = await sweep_detector.analyze(
            current_price=current_price,
            cvd_snapshot=cvd_snapshot_dict,
            vol_delta_snapshot=vol_delta_snapshot,
            liquidation_support=support,
            liquidation_resistance=resistance,
        )

        return signal
    except Exception as exc:
        logger.exception("Error analyzing setup: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
