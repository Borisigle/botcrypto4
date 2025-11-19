"""Models and configuration for Binance websocket ingestion."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel


def _env_bool(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default)
    return value.strip().lower() in {"1", "true", "yes", "on"}


class TradeSide(str, Enum):
    """Normalized aggressor side for trades."""

    BUY = "buy"
    SELL = "sell"


class PriceLevel(BaseModel):
    price: float
    qty: float


class TradeTick(BaseModel):
    ts: datetime
    price: float
    qty: float
    side: TradeSide
    isBuyerMaker: bool
    id: int


class DepthUpdate(BaseModel):
    ts: datetime
    bids: list[PriceLevel]
    asks: list[PriceLevel]
    lastUpdateId: int


class StreamHealth(BaseModel):
    connected: bool
    last_ts: Optional[datetime]


class MetricsView(BaseModel):
    per_minute_count: int
    per_second_rate: float
    queue_size: int


class MetricsSnapshot(BaseModel):
    trades: MetricsView
    depth: MetricsView


@dataclass(slots=True)
class Settings:
    """Runtime configuration sourced from environment variables."""

    symbol: str = field(default_factory=lambda: os.getenv("SYMBOL", "BTCUSDT"))
    depth_interval_ms: int = field(
        default_factory=lambda: int(os.getenv("DEPTH_INTERVAL_MS", "100"))
    )
    max_queue: int = field(default_factory=lambda: int(os.getenv("MAX_QUEUE", "5000")))
    trades_ws_url: Optional[str] = field(default_factory=lambda: os.getenv("TRADES_WS_URL"))
    depth_ws_url: Optional[str] = field(default_factory=lambda: os.getenv("DEPTH_WS_URL"))
    rest_base_url: str = field(
        default_factory=lambda: os.getenv("BINANCE_REST_BASE_URL", "https://fapi.binance.com")
    )
    depth_snapshot_limit: int = field(
        default_factory=lambda: int(os.getenv("DEPTH_SNAPSHOT_LIMIT", "500"))
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    metrics_window_sec: int = field(
        default_factory=lambda: int(os.getenv("METRICS_WINDOW_SEC", "60"))
    )
    context_history_dir: str = field(
        default_factory=lambda: os.getenv("CONTEXT_HISTORY_DIR", "./data/history")
    )
    context_bootstrap_prev_day: bool = field(
        default_factory=lambda: _env_bool("CONTEXT_BOOTSTRAP_PREV_DAY", "true")
    )
    context_fetch_missing_history: bool = field(
        default_factory=lambda: _env_bool("CONTEXT_FETCH_MISSING_HISTORY", "false")
    )
    context_backfill_enabled: bool = field(
        default_factory=lambda: _env_bool("CONTEXT_BACKFILL_ENABLED", "true")
    )
    context_backfill_test_mode: bool = field(
        default_factory=lambda: _env_bool("CONTEXT_BACKFILL_TEST_MODE", "false")
    )
    binance_api_timeout: int = field(
        default_factory=lambda: int(os.getenv("BINANCE_API_TIMEOUT", "30"))
    )
    backfill_max_retries: int = field(
        default_factory=lambda: int(os.getenv("BACKFILL_MAX_RETRIES", "5"))
    )
    backfill_retry_base: float = field(
        default_factory=lambda: float(os.getenv("BACKFILL_RETRY_BASE", "0.5"))
    )
    binance_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("BINANCE_API_KEY")
    )
    binance_api_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("BINANCE_API_SECRET")
    )
    profile_tick_size: float = field(
        default_factory=lambda: float(os.getenv("PROFILE_TICK_SIZE", "0.1"))
    )
    backfill_rate_limit_threshold: int = field(
        default_factory=lambda: int(os.getenv("BACKFILL_RATE_LIMIT_THRESHOLD", "3"))
    )
    backfill_cooldown_seconds: int = field(
        default_factory=lambda: int(os.getenv("BACKFILL_COOLDOWN_SECONDS", "60"))
    )
    backfill_public_delay_ms: int = field(
        default_factory=lambda: int(os.getenv("BACKFILL_PUBLIC_DELAY_MS", "100"))
    )
    backfill_cache_enabled: bool = field(
        default_factory=lambda: _env_bool("BACKFILL_CACHE_ENABLED", "true")
    )
    backfill_cache_dir: str = field(
        default_factory=lambda: os.getenv("BACKFILL_CACHE_DIR", "./context_history_dir/backfill_cache")
    )
    backfill_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("BACKFILL_TIMEOUT_SECONDS", "180"))
    )
    backfill_retry_backoff: float = field(
        default_factory=lambda: float(os.getenv("BACKFILL_RETRY_BACKOFF", "2.0"))
    )
    data_source: str = field(
        default_factory=lambda: os.getenv("DATA_SOURCE", "binance_ws")
    )
    backfill_provider: Optional[str] = field(
        default_factory=lambda: os.getenv("BACKFILL_PROVIDER")
    )
    connector_name: Optional[str] = field(
        default_factory=lambda: os.getenv("CONNECTOR_NAME")
    )
    connector_poll_interval_ms: int = field(
        default_factory=lambda: int(os.getenv("CONNECTOR_POLL_INTERVAL_MS", "100"))
    )
    connector_paper_trading: bool = field(
        default_factory=lambda: _env_bool("CONNECTOR_PAPER_TRADING", "true")
    )
    # Bybit API configuration (for backfill)
    bybit_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("BYBIT_API_KEY")
    )
    bybit_api_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("BYBIT_API_SECRET")
    )
    bybit_rest_base_url: str = field(
        default_factory=lambda: os.getenv("BYBIT_REST_BASE_URL", "https://api.bybit.com")
    )
    bybit_api_timeout: int = field(
        default_factory=lambda: int(os.getenv("BYBIT_API_TIMEOUT", "30"))
    )
    bybit_backfill_max_retries: int = field(
        default_factory=lambda: int(os.getenv("BYBIT_BACKFILL_MAX_RETRIES", "5"))
    )
    bybit_backfill_retry_base: float = field(
        default_factory=lambda: float(os.getenv("BYBIT_BACKFILL_RETRY_BASE", "0.5"))
    )
    bybit_backfill_rate_limit_threshold: int = field(
        default_factory=lambda: int(os.getenv("BYBIT_BACKFILL_RATE_LIMIT_THRESHOLD", "3"))
    )
    bybit_backfill_cooldown_seconds: int = field(
        default_factory=lambda: int(os.getenv("BYBIT_BACKFILL_COOLDOWN_SECONDS", "60"))
    )
    bybit_backfill_public_delay_ms: int = field(
        default_factory=lambda: int(os.getenv("BYBIT_BACKFILL_PUBLIC_DELAY_MS", "50"))
    )
    bybit_backfill_max_concurrent_chunks: int = field(
        default_factory=lambda: int(os.getenv("BYBIT_BACKFILL_MAX_CONCURRENT_CHUNKS", "8"))
    )
    # Bybit connector configuration (for live streaming)
    bybit_connector_config_file: Optional[str] = field(
        default_factory=lambda: os.getenv("BYBIT_CONNECTOR_CONFIG_FILE")
    )
    bybit_connector_testnet: bool = field(
        default_factory=lambda: _env_bool("BYBIT_CONNECTOR_TESTNET", "false")
    )
    # Historical data verification mode
    context_disable_live_data: bool = field(
        default_factory=lambda: _env_bool("CONTEXT_DISABLE_LIVE_DATA", "false")
    )
    context_historical_only_mode: bool = field(
        default_factory=lambda: _env_bool("CONTEXT_HISTORICAL_ONLY_MODE", "false")
    )
    cvd_reset_seconds: int = field(
        default_factory=lambda: int(os.getenv("CVD_RESET_SECONDS", "3600"))
    )
    liquidation_symbol: str = field(
        default_factory=lambda: os.getenv("LIQUIDATION_SYMBOL", "BTCUSDT")
    )
    liquidation_limit: int = field(
        default_factory=lambda: int(os.getenv("LIQUIDATION_LIMIT", "200"))
    )
    liquidation_bin_size: float = field(
        default_factory=lambda: float(os.getenv("LIQUIDATION_BIN_SIZE", "100"))
    )
    liquidation_refresh_seconds: int = field(
        default_factory=lambda: int(os.getenv("LIQUIDATION_REFRESH_SECONDS", "30"))
    )
    liquidation_max_clusters: int = field(
        default_factory=lambda: int(os.getenv("LIQUIDATION_MAX_CLUSTERS", "20"))
    )
    liquidation_category: Optional[str] = field(
        default_factory=lambda: os.getenv("LIQUIDATION_CATEGORY") or None
    )
    liquidation_base_url: str = field(
        default_factory=lambda: os.getenv("LIQUIDATION_BASE_URL", "https://fapi.binance.com")
    )
    liquidation_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("LIQUIDATION_API_KEY") or os.getenv("BINANCE_API_KEY")
    )
    liquidation_api_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("LIQUIDATION_API_SECRET") or os.getenv("BINANCE_API_SECRET")
    )

    def __post_init__(self) -> None:
        base_ws_url = os.getenv("BINANCE_WS_BASE_URL", "wss://fstream.binance.com/ws")
        self.symbol = self.symbol.upper()
        self.liquidation_symbol = self.liquidation_symbol.upper()
        if self.liquidation_bin_size <= 0:
            self.liquidation_bin_size = 100.0
        if self.liquidation_limit <= 0:
            self.liquidation_limit = 200
        if self.liquidation_refresh_seconds <= 0:
            self.liquidation_refresh_seconds = 30
        if self.liquidation_max_clusters <= 0:
            self.liquidation_max_clusters = 20
        if self.liquidation_category:
            category = self.liquidation_category.strip().lower()
            self.liquidation_category = category or None

        interval = max(100, self.depth_interval_ms)
        if interval not in (100, 200, 250, 500, 1000):
            # Binance supports granularities of 100ms or 250ms on perps; fall back gracefully.
            interval = 250 if interval > 100 else 100
        self.depth_interval_ms = interval

        if not self.trades_ws_url:
            self.trades_ws_url = f"{base_ws_url}/{self.symbol.lower()}@aggTrade"
        if not self.depth_ws_url:
            self.depth_ws_url = (
                f"{base_ws_url}/{self.symbol.lower()}@depth@{self.depth_interval_ms}ms"
            )
        self.log_level = self.log_level.upper()

    @property
    def symbol_lower(self) -> str:
        return self.symbol.lower()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
