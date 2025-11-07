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
async def health() -> dict[str, str]:
    """Return basic service status."""
    return {"status": "ok"}
