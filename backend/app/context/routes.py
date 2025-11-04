"""FastAPI routes exposing context metrics."""
from __future__ import annotations

from fastapi import APIRouter

from app.context.service import get_context_service

router = APIRouter()


@router.get("/context")
async def context_view() -> dict:
    service = get_context_service()
    return service.context_payload()


@router.get("/levels")
async def levels_view() -> dict:
    service = get_context_service()
    return service.levels_payload()
