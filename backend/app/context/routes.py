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
