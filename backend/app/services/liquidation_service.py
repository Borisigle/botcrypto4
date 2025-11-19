"""Liquidation tracker service backed by Bybit public API."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List, Optional

import httpx

from app.models.indicators import LiquidationCluster, LiquidationSnapshot

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
        category: Optional[str] = "linear",
        base_url: str = "https://api.bybit.com",
        http_timeout: float = 10.0,
    ) -> None:
        self.symbol = symbol.upper()
        self.limit = limit
        self.bin_size = bin_size if bin_size > 0 else 100.0
        self.max_clusters = max(1, max_clusters)
        self.category = category
        self.endpoint = f"{base_url.rstrip('/')}/v5/market/liquidation"
        self.http_timeout = http_timeout

        self.liquidations: List[dict] = []
        self.clusters: Dict[float, ClusterBucket] = {}
        self._last_updated: Optional[datetime] = None

        self.logger = logging.getLogger("liquidation_service")
        self._lock = Lock()

    @property
    def last_updated(self) -> Optional[datetime]:
        with self._lock:
            return self._last_updated

    async def fetch_liquidations(self) -> None:
        """Fetch the most recent liquidation events from Bybit."""

        params = {
            "symbol": self.symbol,
            "limit": self.limit,
        }
        if self.category:
            params["category"] = self.category

        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.get(self.endpoint, params=params)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self.logger.warning("Failed to fetch Bybit liquidations: %s", exc)
            return

        payload = response.json()
        if payload.get("retCode") not in (0, "0", None):
            self.logger.warning(
                "Unexpected retCode from Bybit liquidation API: retCode=%s retMsg=%s",
                payload.get("retCode"),
                payload.get("retMsg"),
            )
            return

        liq_list = payload.get("result", {}).get("list", []) or []
        normalized = [entry for entry in (self._normalize_liquidation(item) for item in liq_list) if entry]

        with self._lock:
            self.liquidations = normalized
            self._build_clusters_locked()
            self._last_updated = datetime.now(timezone.utc)
            cluster_count = len(self.clusters)

        self.logger.info("Liquidations fetched: %s items", len(normalized))
        self.logger.debug("Liquidation clusters updated: unique_bins=%s", cluster_count)

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
        try:
            price = float(entry.get("price"))
            qty = float(entry.get("qty"))
        except (TypeError, ValueError):
            return None

        if qty <= 0:
            return None

        side = str(entry.get("side", "")).lower()
        if side not in {"buy", "sell"}:
            return None
        return {"price": price, "qty": qty, "side": side}

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


def init_liquidation_service(**kwargs) -> LiquidationService:
    global _liquidation_service
    if _liquidation_service is None:
        _liquidation_service = LiquidationService(**kwargs)
    return _liquidation_service


def get_liquidation_service() -> LiquidationService:
    if _liquidation_service is None:
        raise RuntimeError("LiquidationService has not been initialized yet")
    return _liquidation_service
