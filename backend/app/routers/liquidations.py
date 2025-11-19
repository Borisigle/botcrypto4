"""Routes exposing liquidation cluster data."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.liquidation_service import LiquidationService, get_liquidation_service

router = APIRouter(prefix="/liquidations", tags=["liquidations"])


def _get_liquidation_service() -> LiquidationService:
    return get_liquidation_service()


@router.get("/clusters")
async def get_liquidation_clusters(
    service: LiquidationService = Depends(_get_liquidation_service),
) -> dict:
    """Return the top liquidation clusters grouped by price level."""
    try:
        return service.get_clusters()
    except Exception as exc:  # pragma: no cover - safeguard for unexpected errors
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/support-resistance")
async def get_support_resistance(
    current_price: float = Query(..., gt=0),
    service: LiquidationService = Depends(_get_liquidation_service),
) -> dict:
    """Return nearest support and resistance derived from liquidation clusters."""
    try:
        support = service.get_nearest_support(current_price)
        resistance = service.get_nearest_resistance(current_price)
        timestamp = service.last_updated or datetime.now(timezone.utc)
        return {
            "current_price": current_price,
            "support": support,
            "resistance": resistance,
            "timestamp": timestamp,
        }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/refresh")
async def refresh_liquidations(
    service: LiquidationService = Depends(_get_liquidation_service),
) -> dict:
    """Force a refresh of liquidation data from Coinglass API."""
    try:
        await service.fetch_liquidations()
        return {
            "status": "liquidations refreshed",
            "count": service.get_liquidation_count(),
        }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc
