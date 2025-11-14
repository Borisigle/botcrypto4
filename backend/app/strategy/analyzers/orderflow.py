"""Order flow analyzer for real-time market metrics calculation."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.ws.models import Settings, TradeTick, get_settings

from ..metrics import MetricsCalculator

logger = logging.getLogger("orderflow_analyzer")


class OrderFlowAnalyzer:
    """Analyzes order flow using real-time trade data and calculates market metrics."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        metrics_calculator: Optional[MetricsCalculator] = None,
        calculation_interval: int = 50,
        tick_size: Optional[float] = None,
    ) -> None:
        """Initialize OrderFlowAnalyzer.

        Args:
            settings: Application settings
            metrics_calculator: MetricsCalculator instance (optional)
            calculation_interval: Number of trades before recalculating metrics (default 50)
            tick_size: Price tick size for binning. Defaults to the calculator's tick size when omitted.
        """
        self.settings = settings or get_settings()
        if metrics_calculator is None:
            calc_tick_size = tick_size if tick_size is not None else 0.1
            self.metrics_calculator = MetricsCalculator(tick_size=calc_tick_size)
        else:
            self.metrics_calculator = metrics_calculator

        self.calculation_interval = calculation_interval
        self.tick_size = tick_size if tick_size is not None else self.metrics_calculator.tick_size

        # Cumulative state for incremental calculation
        self._sum_price_qty: float = 0.0
        self._sum_qty: float = 0.0
        self._volume_by_price: defaultdict[float, float] = defaultdict(float)
        self._buy_volume: float = 0.0
        self._sell_volume: float = 0.0
        self._trade_count: int = 0

        # Latest metrics
        self._latest_metrics: Optional[Dict[str, Any]] = None
        self._last_metrics_update: Optional[datetime] = None

    def ingest_trade(self, trade: TradeTick) -> None:
        """Ingest a trade tick and update metrics incrementally.

        Args:
            trade: TradeTick from websocket stream
        """
        price = float(trade.price)
        qty = float(trade.qty)
        is_buyer_maker = trade.isBuyerMaker

        # Update cumulative VWAP state
        self._sum_price_qty += price * qty
        self._sum_qty += qty

        # Update volume by price for POC calculation
        price_bin = self._bin_price(price)
        self._volume_by_price[price_bin] += qty

        # Update buy/sell volumes for delta
        if is_buyer_maker:
            self._sell_volume += qty
        else:
            self._buy_volume += qty

        self._trade_count += 1

        # Recalculate metrics every N trades
        if self._trade_count % self.calculation_interval == 0:
            self._update_metrics()

    def _bin_price(self, price: float) -> float:
        """Bin a price to the nearest tick size."""
        if self.tick_size <= 0:
            return price
        return round(price / self.tick_size) * self.tick_size

    def _update_metrics(self) -> None:
        """Recalculate metrics from cumulative state (incremental)."""
        try:
            vwap = self._sum_price_qty / self._sum_qty if self._sum_qty > 0 else None

            # Calculate POC from volume_by_price
            poc = None
            if self._volume_by_price:
                poc, _ = max(self._volume_by_price.items(), key=lambda item: item[1])

            # Calculate delta
            delta = self._buy_volume - self._sell_volume

            # Calculate footprint (top 20 price bins)
            footprint = self._calculate_footprint()

            self._latest_metrics = {
                "vwap": vwap,
                "poc": poc,
                "delta": delta,
                "buy_volume": self._buy_volume,
                "sell_volume": self._sell_volume,
                "footprint": footprint,
                "trade_count": self._trade_count,
            }
            self._last_metrics_update = datetime.now(timezone.utc)

            if vwap is not None and poc is not None:
                logger.debug(
                    "Metrics updated (incremental): VWAP=%.2f, POC=%.2f, Delta=%.2f, Trades=%d",
                    vwap,
                    poc,
                    delta,
                    self._trade_count,
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Error updating metrics: %s", exc)

    def _calculate_footprint(self) -> List[Dict[str, Any]]:
        """Calculate footprint from volume_by_price (top 20 bins)."""
        if not self._volume_by_price:
            return []

        try:
            sorted_bins = sorted(
                self._volume_by_price.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:20]

            footprint: List[Dict[str, Any]] = []
            for rank, (price, volume) in enumerate(sorted_bins, start=1):
                footprint.append(
                    {
                        "price": price,
                        "volume": volume,
                        "buy_vol": 0.0,
                        "sell_vol": 0.0,
                        "rank": rank,
                    }
                )

            return footprint
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Error calculating footprint: %s", exc)
            return []

    def get_latest_metrics(self) -> Optional[Dict[str, Any]]:
        """Get the latest calculated metrics."""
        return self._latest_metrics

    def get_metrics_with_metadata(self) -> Dict[str, Any]:
        """Get metrics with additional metadata for API responses."""
        return {
            "metrics": self._latest_metrics,
            "metadata": {
                "last_update": self._last_metrics_update.isoformat()
                if self._last_metrics_update
                else None,
                "trade_count": self._trade_count,
                "cumulative_volume": self._sum_qty,
            },
        }

    def initialize_from_backfill(self, trades: List[TradeTick]) -> None:
        """Initialize cumulative state from backfilled trades."""
        logger.info("Initializing OrderFlowAnalyzer from backfill with %d trades", len(trades))

        for trade in trades:
            price = float(trade.price)
            qty = float(trade.qty)
            is_buyer_maker = trade.isBuyerMaker

            self._sum_price_qty += price * qty
            self._sum_qty += qty

            price_bin = self._bin_price(price)
            self._volume_by_price[price_bin] += qty

            if is_buyer_maker:
                self._sell_volume += qty
            else:
                self._buy_volume += qty

            self._trade_count += 1

        self._update_metrics()

        if self._latest_metrics:
            logger.info(
                "Backfill initialization complete: VWAP=%s, POC=%s, Trades=%d",
                self._format_optional(self._latest_metrics.get("vwap")),
                self._format_optional(self._latest_metrics.get("poc")),
                self._trade_count,
            )

    def initialize_from_state(
        self,
        *,
        sum_price_qty: float,
        sum_qty: float,
        volume_by_price: Dict[float, float],
        buy_volume: float,
        sell_volume: float,
        trade_count: int,
    ) -> None:
        """Initialize cumulative state from pre-calculated values."""
        logger.info("Initializing OrderFlowAnalyzer from state (trades=%d)", trade_count)

        self._sum_price_qty = sum_price_qty
        self._sum_qty = sum_qty
        self._volume_by_price = defaultdict(float, volume_by_price)
        self._buy_volume = buy_volume
        self._sell_volume = sell_volume
        self._trade_count = trade_count

        self._update_metrics()

        if self._latest_metrics:
            logger.info(
                "State initialization complete: VWAP=%s, POC=%s",
                self._format_optional(self._latest_metrics.get("vwap")),
                self._format_optional(self._latest_metrics.get("poc")),
            )

    def reset_state(self) -> None:
        """Reset cumulative state (e.g., on day boundary)."""
        self._sum_price_qty = 0.0
        self._sum_qty = 0.0
        self._volume_by_price.clear()
        self._buy_volume = 0.0
        self._sell_volume = 0.0
        self._trade_count = 0
        self._latest_metrics = None
        self._last_metrics_update = None
        logger.info("OrderFlowAnalyzer state reset")

    def reset_buffer(self) -> None:
        """Alias for reset_state to maintain backwards compatibility."""
        self.reset_state()

    @staticmethod
    def _format_optional(value: Optional[float]) -> str:
        return f"{value:.2f}" if value is not None else "--"


# Global instance
_analyzer_instance: Optional[OrderFlowAnalyzer] = None


def get_orderflow_analyzer() -> OrderFlowAnalyzer:
    """Get the global OrderFlowAnalyzer instance."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = OrderFlowAnalyzer()
    return _analyzer_instance
