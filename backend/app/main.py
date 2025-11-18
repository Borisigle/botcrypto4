import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.trades import router as trades_router
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

ws_module = get_ws_module()


@app.on_event("startup")
async def startup_event() -> None:
    await ws_module.startup()


@app.on_event("shutdown")
async def shutdown_event() -> None:
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