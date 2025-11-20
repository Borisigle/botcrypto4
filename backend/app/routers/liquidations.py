"""Routes exposing liquidation cluster data."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.liquidation_service import LiquidationService, get_liquidation_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/liquidations", tags=["liquidations"])


def _get_liquidation_service() -> LiquidationService:
    return get_liquidation_service()


@router.get("/clusters")
async def get_liquidation_clusters(
    service: LiquidationService = Depends(_get_liquidation_service),
) -> dict:
    """Return the top liquidation clusters grouped by price level."""
    try:
        clusters = service.get_clusters()
        logger.info(
            f"Liquidation clusters requested: {len(clusters)} levels, "
            f"total liquidations: {service.get_liquidation_count()}"
        )
        return clusters
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
    """Force a refresh of liquidation data from Binance Futures."""
    try:
        await service.fetch_liquidations()
        return {
            "status": "liquidations refreshed",
            "count": service.get_liquidation_count(),
        }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/debug")
async def get_liquidation_debug(
    service: LiquidationService = Depends(_get_liquidation_service),
) -> dict:
    """Get debug information about liquidation service status."""
    try:
        ws_connected = service.ws_connector.is_connected if service.ws_connector else False
        total_liq = service.get_liquidation_count()
        clusters = service.get_clusters()
        
        # Get last 10 liquidations for inspection
        with service._lock:
            recent_liquidations = list(service.liquidations)[-10:]
        
        debug_info = {
            "ws_connected": ws_connected,
            "total_liquidations": total_liq,
            "clusters_count": len(clusters),
            "recent_liquidations": recent_liquidations,
            "top_clusters": clusters,
        }
        
        logger.info(
            f"Debug info requested: ws_connected={ws_connected}, "
            f"total_liquidations={total_liq}, clusters_count={len(clusters)}"
        )
        
        return debug_info
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc
