"""FastAPI routes exposing context metrics."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from app.context.service import get_context_service

router = APIRouter()


@router.get("/context")
async def context_view(vwap_mode: Literal["base", "quote"] = "base") -> dict:
    service = get_context_service()
    return service.context_payload(vwap_mode=vwap_mode)


@router.get("/levels")
async def levels_view(vwap_mode: Literal["base", "quote"] = "base") -> dict:
    service = get_context_service()
    return service.levels_payload(vwap_mode=vwap_mode)


@router.get("/price")
async def price_view() -> dict:
    service = get_context_service()
    return service.price_payload()


@router.get("/debug/vwap")
async def debug_vwap_view() -> dict:
    service = get_context_service()
    return service.debug_vwap_payload()


@router.get("/debug/poc")
async def debug_poc_view() -> dict:
    service = get_context_service()
    return service.debug_poc_payload()


@router.get("/debug/exchangeinfo")
async def debug_exchange_info_view() -> dict:
    service = get_context_service()
    return service.debug_exchange_info_payload()


@router.get("/backfill/status")
async def backfill_status_view() -> dict:
    service = get_context_service()
    return service.get_backfill_status()


@router.get("/debug/trades")
async def debug_trades_view() -> dict:
    service = get_context_service()
    return service.debug_trades_payload()


@router.get("/debug/recalculate-verification")
async def recalculate_verification_view() -> dict:
    """Debug endpoint to verify VWAP/POC calculations match expectations."""
    service = get_context_service()
    
    # Get current calculations
    vwap_current = service._current_vwap("base")
    poc_current = service.poc_price
    
    # Recalculate from volume map
    if service.volume_by_price:
        total_volume = sum(service.volume_by_price.values())
        total_price_qty = sum(p * v for p, v in service.volume_by_price.items())
        vwap_recalc = total_price_qty / total_volume if total_volume > 0 else None
        
        # Find POC again
        poc_recalc = max(service.volume_by_price.items(), key=lambda x: (x[1], -x[0]))[0] if service.volume_by_price else None
    else:
        vwap_recalc = None
        poc_recalc = None
    
    # Calculate verification metrics
    vwap_match = abs(vwap_current - vwap_recalc) < 0.001 if vwap_current and vwap_recalc else vwap_current == vwap_recalc
    poc_match = poc_current == poc_recalc
    
    return {
        "verification": {
            "vwap": {
                "current": service._format_float(vwap_current),
                "recalculated": service._format_float(vwap_recalc),
                "match": vwap_match,
            },
            "poc": {
                "current": service._format_float(poc_current),
                "recalculated": service._format_float(poc_recalc),
                "match": poc_match,
            },
        },
        "volume_profile": {
            "total_volume": service._format_float(total_volume) if service.volume_by_price else "0",
            "price_levels": len(service.volume_by_price),
            "day_high": service._format_float(service.day_high),
            "day_low": service._format_float(service.day_low),
        },
        "data_integrity": {
            "sum_price_qty_matches": abs(service.sum_price_qty_base - total_price_qty) < 0.001 if service.volume_by_price else True,
            "sum_qty_matches": abs(service.sum_qty_base - total_volume) < 0.001 if service.volume_by_price else True,
        },
    }
