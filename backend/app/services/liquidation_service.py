"""Liquidation tracker service backed by Binance Futures API."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, Dict, Optional

import httpx

from app.connectors.liquidation_websocket import LiquidationWebSocketConnector
from app.models.indicators import LiquidationCluster, LiquidationSnapshot
from app.utils.binance_signer import BinanceSigner

ClusterBucket = Dict[str, float]


class LiquidationService:
    """Fetches liquidation data and builds price-level clusters."""

    def __init__(
        self,
        *,
        symbol: str = "BTCUSDT",
        limit: int = 200,
        bin_size: float = 100.0,
        max_clusters: int = 20,
        category: Optional[str] = None,
        base_url: str = "https://fapi.binance.com",
        http_timeout: float = 10.0,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        websocket_enabled: bool = True,
        max_liquidations: int = 500,
    ) -> None:
        self.symbol = symbol.upper()
        self.limit = limit
        self.bin_size = bin_size if bin_size > 0 else 100.0
        self.max_clusters = max(1, max_clusters)
        self.category = category
        self.endpoint = f"{base_url.rstrip('/')}/fapi/v1/forceOrders"
        self.http_timeout = http_timeout
        self.websocket_enabled = websocket_enabled

        # Use deque for efficient append and automatic size limiting
        self.liquidations: Deque[dict] = deque(maxlen=max_liquidations)
        self.clusters: Dict[float, ClusterBucket] = {}
        self._last_updated: Optional[datetime] = None
        self._last_cluster_build: Optional[datetime] = None

        self.logger = logging.getLogger("liquidation_service")
        self._lock = Lock()

        self.signer: Optional[BinanceSigner] = None
        if api_key and api_secret:
            self.signer = BinanceSigner(api_key, api_secret)
            self.logger.info("Liquidation service initialized with authenticated API credentials")
        
        # WebSocket connector and background tasks
        self.ws_connector: Optional[LiquidationWebSocketConnector] = None
        self._cluster_rebuild_task: Optional[asyncio.Task] = None
        self._ws_task: Optional[asyncio.Task] = None

    @property
    def last_updated(self) -> Optional[datetime]:
        with self._lock:
            return self._last_updated

    async def fetch_liquidations(self) -> None:
        """Fetch the most recent liquidation events from Binance Futures API."""

        params = {
            "symbol": self.symbol,
            "limit": self.limit,
        }

        headers = {}
        if self.signer:
            params = self.signer.sign_request(params)
            headers["X-MBX-APIKEY"] = self.signer.api_key

        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.get(self.endpoint, params=params, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self.logger.warning("Failed to fetch Binance liquidations: %s", exc)
            return

        try:
            data = response.json()
        except Exception as exc:
            self.logger.warning("Failed to parse Binance liquidation response: %s", exc)
            return

        liq_list = data if isinstance(data, list) else []
        normalized = [entry for entry in (self._normalize_liquidation(item) for item in liq_list) if entry]

        with self._lock:
            self.liquidations.clear()
            self.liquidations.extend(normalized)
            self._build_clusters_locked()
            self._last_updated = datetime.now(timezone.utc)
            self._last_cluster_build = self._last_updated
            cluster_count = len(self.clusters)

        auth_status = "authenticated" if self.signer else "unauthenticated"
        self.logger.info("Liquidations fetched from Binance (%s): %s items", auth_status, len(normalized))
        self.logger.debug("Liquidation clusters updated: unique_bins=%s", cluster_count)
    
    async def initialize(self, cluster_rebuild_interval: int = 5) -> None:
        """Initialize WebSocket connector for real-time liquidations.
        
        Args:
            cluster_rebuild_interval: Seconds between automatic cluster rebuilds
        """
        if not self.websocket_enabled:
            self.logger.info("Liquidation WebSocket disabled, using REST API only")
            return
        
        if self.ws_connector:
            self.logger.warning("Liquidation WebSocket already initialized")
            return
        
        self.ws_connector = LiquidationWebSocketConnector(
            symbol=self.symbol.lower(),
            on_liquidation=self._on_liquidation_received,
        )
        
        # Start WebSocket in background
        self._ws_task = asyncio.create_task(self.ws_connector.connect())
        
        # Start cluster rebuild task
        self._cluster_rebuild_task = asyncio.create_task(self._cluster_rebuild_loop(cluster_rebuild_interval))
        
        self.logger.info(
            "Liquidation WebSocket connector initialized (symbol=%s, cluster_rebuild_interval=%ss)",
            self.symbol,
            cluster_rebuild_interval,
        )
    
    async def _on_liquidation_received(self, liquidation: dict) -> None:
        """Callback when new liquidation is received from WebSocket.
        
        Args:
            liquidation: Liquidation dict with keys: price, qty, side, time, symbol
        """
        normalized = self._normalize_liquidation(liquidation)
        if not normalized:
            return
        
        with self._lock:
            self.liquidations.append(normalized)
            self._last_updated = datetime.now(timezone.utc)
            liq_count = len(self.liquidations)
        
        # Trigger cluster rebuild on every 10th liquidation or every few seconds
        if liq_count % 10 == 0:
            self._maybe_rebuild_clusters()
    
    def _maybe_rebuild_clusters(self) -> None:
        """Rebuild clusters if enough time has passed since last rebuild."""
        now = datetime.now(timezone.utc)
        
        # Rebuild if it's been more than 2 seconds since last rebuild
        if self._last_cluster_build is None or \
           (now - self._last_cluster_build).total_seconds() >= 2:
            with self._lock:
                self._build_clusters_locked()
                self._last_cluster_build = now
                cluster_count = len(self.clusters)
                liq_count = len(self.liquidations)
            
            self.logger.debug("Clusters rebuilt: %s bins, %s liquidations", cluster_count, liq_count)
    
    async def _cluster_rebuild_loop(self, interval: int) -> None:
        """Background task that periodically rebuilds clusters.
        
        Args:
            interval: Seconds between rebuilds
        """
        self.logger.info("Cluster rebuild loop started (interval=%ss)", interval)
        
        while True:
            try:
                await asyncio.sleep(interval)
                self._maybe_rebuild_clusters()
            except asyncio.CancelledError:
                self.logger.info("Cluster rebuild loop cancelled")
                break
            except Exception as exc:
                self.logger.exception("Error in cluster rebuild loop: %s", exc)
    
    async def shutdown(self) -> None:
        """Shutdown WebSocket connector and cleanup resources."""
        # Cancel cluster rebuild task
        if self._cluster_rebuild_task:
            self._cluster_rebuild_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cluster_rebuild_task
            self._cluster_rebuild_task = None
            self.logger.info("Cluster rebuild task stopped")
        
        # Close WebSocket connector
        if self.ws_connector:
            await self.ws_connector.close()
            self.ws_connector = None
        
        # Cancel WebSocket task
        if self._ws_task:
            self._ws_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None
            self.logger.info("Liquidation WebSocket connector closed")

    def _build_clusters_locked(self) -> None:
        clusters: Dict[float, ClusterBucket] = {}
        for liq in self.liquidations:
            price = liq["price"]
            qty = liq["qty"]
            side = liq["side"]

            bin_key = round(price / self.bin_size) * self.bin_size
            bucket = clusters.setdefault(
                bin_key,
                {"buy": 0.0, "sell": 0.0, "total": 0.0, "ratio": 0.0},
            )

            if side == "buy":
                bucket["buy"] += qty
            elif side == "sell":
                bucket["sell"] += qty
            bucket["total"] += qty

        for bucket in clusters.values():
            bucket["ratio"] = self._calculate_ratio(bucket["buy"], bucket["sell"])

        self.clusters = clusters

    @staticmethod
    def _calculate_ratio(buy_volume: float, sell_volume: float) -> float:
        if sell_volume <= 0:
            return buy_volume if buy_volume > 0 else 0.0
        return buy_volume / sell_volume

    @staticmethod
    def _normalize_liquidation(entry: dict) -> Optional[dict]:
        if not entry:
            return None

        price_value = entry.get("price") or entry.get("p")
        qty_value = entry.get("origQty") or entry.get("qty") or entry.get("q")
        side_value = entry.get("side") or entry.get("S")

        try:
            price = float(price_value)
            qty = float(qty_value)
        except (TypeError, ValueError):
            return None

        if qty <= 0:
            return None

        side: Optional[str] = None
        if isinstance(side_value, str):
            side_upper = side_value.upper()
            if side_upper in {"BUY", "SELL"}:
                side = side_upper.lower()

        if side is None:
            return None

        time_value = entry.get("time") or entry.get("T")
        timestamp: datetime
        if isinstance(time_value, datetime):
            timestamp = time_value if time_value.tzinfo else time_value.replace(tzinfo=timezone.utc)
        else:
            try:
                timestamp = datetime.fromtimestamp(int(time_value) / 1000, tz=timezone.utc) if time_value else datetime.now(timezone.utc)
            except (TypeError, ValueError):
                timestamp = datetime.now(timezone.utc)

        symbol_value = entry.get("symbol") or entry.get("s") or "UNKNOWN"

        return {
            "price": price,
            "qty": qty,
            "side": side,
            "time": timestamp,
            "symbol": str(symbol_value).upper(),
        }

    def get_clusters(self) -> Dict[float, ClusterBucket]:
        """Return the top clusters ordered by total liquidation volume."""

        with self._lock:
            cluster_items = list(self.clusters.items())

        sorted_clusters = sorted(
            cluster_items,
            key=lambda item: item[1]["total"],
            reverse=True,
        )
        top_clusters = sorted_clusters[: self.max_clusters]
        return {
            float(price): {
                "buy": bucket["buy"],
                "sell": bucket["sell"],
                "total": bucket["total"],
                "ratio": bucket["ratio"],
            }
            for price, bucket in top_clusters
        }

    def get_nearest_support(self, current_price: float) -> Optional[float]:
        with self._lock:
            supports = [price for price in self.clusters.keys() if price < current_price]
        return max(supports) if supports else None

    def get_nearest_resistance(self, current_price: float) -> Optional[float]:
        with self._lock:
            resistances = [price for price in self.clusters.keys() if price > current_price]
        return min(resistances) if resistances else None

    def build_snapshot(self, current_price: Optional[float] = None) -> LiquidationSnapshot:
        clusters_dict = self.get_clusters()
        clusters = [
            LiquidationCluster(
                price_level=price,
                buy_liquidations=data.get("buy", 0.0),
                sell_liquidations=data.get("sell", 0.0),
                total_liquidations=data.get("total", 0.0),
                ratio=data.get("ratio", 0.0),
            )
            for price, data in clusters_dict.items()
        ]

        support = self.get_nearest_support(current_price) if current_price is not None else None
        resistance = self.get_nearest_resistance(current_price) if current_price is not None else None
        timestamp = self.last_updated or datetime.now(timezone.utc)

        return LiquidationSnapshot(
            clusters=clusters,
            nearest_support=support,
            nearest_resistance=resistance,
            timestamp=timestamp,
        )

    def get_liquidation_count(self) -> int:
        with self._lock:
            return len(self.liquidations)


_liquidation_service: Optional[LiquidationService] = None


def init_liquidation_service(
    *,
    symbol: str = "BTCUSDT",
    limit: int = 200,
    bin_size: float = 100.0,
    max_clusters: int = 20,
    category: Optional[str] = None,
    base_url: str = "https://fapi.binance.com",
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    websocket_enabled: bool = True,
    max_liquidations: int = 500,
) -> LiquidationService:
    """Initialize the liquidation service with optional authentication.
    
    Args:
        symbol: Trading symbol (e.g., BTCUSDT)
        limit: Number of liquidations to fetch
        bin_size: Price bin size for clustering
        max_clusters: Maximum clusters to return
        category: Optional liquidation category
        base_url: Binance API base URL
        api_key: Optional Binance API key for authenticated requests
        api_secret: Optional Binance API secret for authenticated requests
        websocket_enabled: Enable real-time WebSocket liquidation streaming
        max_liquidations: Maximum liquidations to keep in memory
    """
    global _liquidation_service
    if _liquidation_service is None:
        _liquidation_service = LiquidationService(
            symbol=symbol,
            limit=limit,
            bin_size=bin_size,
            max_clusters=max_clusters,
            category=category,
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            websocket_enabled=websocket_enabled,
            max_liquidations=max_liquidations,
        )
    return _liquidation_service


def get_liquidation_service() -> LiquidationService:
    if _liquidation_service is None:
        raise RuntimeError("LiquidationService has not been initialized yet")
    return _liquidation_service
