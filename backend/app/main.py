import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.context.routes import router as context_router
from app.context.service import get_context_service
from app.strategy.engine import get_strategy_engine
from app.strategy.routes import router as strategy_router
from app.ws.models import get_settings
from app.ws.routes import get_ws_module, router as ws_router

load_dotenv()

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(message)s",
)

app = FastAPI(title="Botcrypto4 Backend")

allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(context_router)
app.include_router(ws_router)
app.include_router(strategy_router)

context_service = get_context_service()
ws_module = get_ws_module()
strategy_engine = get_strategy_engine()

# Connect strategy engine to WS module for trade ingestion
ws_module.set_strategy_engine(strategy_engine)

STATUS_MESSAGES = {
    "london": "🇬🇧 LONDON SESSION ACTIVE (08:00-12:00 UTC)",
    "overlap": "🌍 NY OVERLAP ACTIVE (13:00-17:00 UTC)",
    "waiting_for_session": "⏳ ESPERANDO LONDON - NO OPERAR",
    "off": "⏳ NO SESSION",
}


@app.on_event("startup")
async def startup_event() -> None:
    await context_service.startup()
    await ws_module.startup()
    await strategy_engine.startup()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await strategy_engine.shutdown()
    await ws_module.shutdown()
    await context_service.shutdown()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def _build_readiness_payload() -> dict:
    engine = get_strategy_engine()
    scheduler_state = engine.scheduler.get_session_info()

    backfill_progress = context_service.backfill_progress.copy()
    backfill_complete = context_service.backfill_complete

    is_session_active = scheduler_state.get("is_active", False)
    trading_enabled = backfill_complete and is_session_active

    if not backfill_complete:
        if backfill_progress.get("status") == "in_progress":
            metrics_precision = f"IMPRECISE (backfill {backfill_progress.get('percentage', 0):.0f}%)"
        else:
            metrics_precision = "IMPRECISE (backfill pending)"
    else:
        metrics_precision = "PRECISE"

    session_status = scheduler_state.get("current_session", "off")

    return {
        "status": "ok",
        "session": session_status,
        "session_message": STATUS_MESSAGES.get(session_status, "Unknown"),
        "is_trading_active": scheduler_state.get("is_active", False),
        "trading_enabled": trading_enabled,
        "backfill_complete": backfill_complete,
        "backfill_status": backfill_progress.get("status", "idle"),
        "backfill_progress": {
            "current": backfill_progress.get("current", 0),
            "total": backfill_progress.get("total", 0),
            "percentage": backfill_progress.get("percentage", 0.0),
            "estimated_seconds_remaining": backfill_progress.get("estimated_seconds_remaining"),
        },
        "metrics_precision": metrics_precision,
    }


@app.get("/ready")
async def ready() -> dict:
    return _build_readiness_payload()
