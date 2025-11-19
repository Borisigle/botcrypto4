import asyncio
import logging
import os
from contextlib import suppress
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.indicators import router as indicators_router
from app.routers.trades import router as trades_router
from app.services.cvd_service import init_cvd_service, get_cvd_service
from app.services.volume_delta_service import init_volume_delta_service, get_volume_delta_service
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

app.include_router(ws_router)
app.include_router(trades_router)
app.include_router(indicators_router)

ws_module = get_ws_module()
_cvd_reset_task: Optional[asyncio.Task] = None
_volume_delta_snapshot_task: Optional[asyncio.Task] = None
logger = logging.getLogger("main")


async def _cvd_auto_reset_loop() -> None:
    """Background task that periodically checks and resets CVD if needed."""
    loop_logger = logging.getLogger("cvd_service")
    loop_logger.info("CVD auto-reset loop started (check_interval=%ss)", 60)
    while True:
        try:
            await asyncio.sleep(60)
            cvd_service = get_cvd_service()
            if cvd_service.maybe_reset():
                loop_logger.info("CVD auto-reset executed")
        except asyncio.CancelledError:
            loop_logger.info("CVD auto-reset loop cancelled")
            break
        except Exception:
            loop_logger.exception("CVD auto-reset loop encountered an error")


async def _volume_delta_snapshot_loop() -> None:
    """Background task that periodically records Volume Delta snapshots."""
    loop_logger = logging.getLogger("volume_delta_service")
    loop_logger.info("Volume Delta snapshot loop started (interval=%ss)", 10)
    while True:
        try:
            await asyncio.sleep(10)
            volume_delta_service = get_volume_delta_service()
            # Record default period snapshot (1 minute)
            trades = ws_module.trade_service.get_recent_trades(limit=999_999)
            delta_data = volume_delta_service.calculate_volume_delta(trades, 60)
            volume_delta_service.record_snapshot(delta_data)
        except asyncio.CancelledError:
            loop_logger.info("Volume Delta snapshot loop cancelled")
            break
        except Exception:
            loop_logger.exception("Volume Delta snapshot loop encountered an error")


@app.on_event("startup")
async def startup_event() -> None:
    global _cvd_reset_task, _volume_delta_snapshot_task

    init_cvd_service(reset_period_seconds=settings.cvd_reset_seconds)
    logger.info(
        "CVD service initialized (reset_period=%ss)",
        settings.cvd_reset_seconds,
    )

    init_volume_delta_service(period_seconds=60)
    logger.info("Volume Delta service initialized (default_period=60s)")

    await ws_module.startup()

    _cvd_reset_task = asyncio.create_task(_cvd_auto_reset_loop())
    logger.info("CVD auto-reset background task started")

    _volume_delta_snapshot_task = asyncio.create_task(_volume_delta_snapshot_loop())
    logger.info("Volume Delta snapshot background task started")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global _cvd_reset_task, _volume_delta_snapshot_task

    if _cvd_reset_task:
        _cvd_reset_task.cancel()
        with suppress(asyncio.CancelledError):
            await _cvd_reset_task
        logger.info("CVD auto-reset background task stopped")
        _cvd_reset_task = None

    if _volume_delta_snapshot_task:
        _volume_delta_snapshot_task.cancel()
        with suppress(asyncio.CancelledError):
            await _volume_delta_snapshot_task
        logger.info("Volume Delta snapshot background task stopped")
        _volume_delta_snapshot_task = None

    await ws_module.shutdown()


@app.get("/health")
async def health() -> dict:
    ws_health = ws_module.health_payload()
    return {
        "status": "Healthy",
        "websocket_connected": ws_health.get("trades", {}).get("connected", False),
    }


@app.get("/ready")
async def ready() -> dict:
    return {
        "status": "Ready",
    }
