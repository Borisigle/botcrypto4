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
        tick_size: float = 0.01,
    ) -> None:
        """Initialize OrderFlowAnalyzer.

        Args:
            settings: Application settings
            metrics_calculator: MetricsCalculator instance (optional)
            calculation_interval: Number of trades before recalculating metrics (default 50)
            tick_size: Price tick size for binning (default 0.01)
        """
        self.settings = settings or get_settings()
        self.metrics_calculator = metrics_calculator or MetricsCalculator(tick_size=tick_size)
        self.calculation_interval = calculation_interval
        self.tick_size = tick_size

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
        """Bin a price to the nearest tick size.

        Args:
            price: Raw price value

        Returns:
            Binned price value
        """
        return round(price / self.tick_size) * self.tick_size

    def _update_metrics(self) -> None:
        """Recalculate metrics from cumulative state (incremental)."""
        try:
            # Calculate VWAP from cumulative sums
            vwap = self._sum_price_qty / self._sum_qty if self._sum_qty > 0 else None

            # Calculate POC from volume_by_price
            poc = None
            poc_volume = 0.0
            if self._volume_by_price:
                poc, poc_volume = max(self._volume_by_price.items(), key=lambda x: x[1])

            # Calculate delta
            delta = self._buy_volume - self._sell_volume

            # Calculate footprint (top 20 price bins)
            footprint = self._calculate_footprint()

            # Store metrics
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

            # Log the update
            if vwap is not None and poc is not None:
                logger.debug(
                    "Metrics updated (incremental): VWAP=%.2f, POC=%.2f, Delta=%.2f, Trades=%d",
                    vwap,
                    poc,
                    delta,
                    self._trade_count,
                )
        except Exception as exc:
            logger.exception("Error updating metrics: %s", exc)

    def _calculate_footprint(self) -> List[Dict[str, Any]]:
        """Calculate footprint from volume_by_price (top 20 bins).

        Returns:
            List of top 20 price bins sorted by volume
        """
        if not self._volume_by_price:
            return []

        try:
            # Sort by volume descending and take top 20
            sorted_bins = sorted(
                self._volume_by_price.items(),
                key=lambda x: x[1],
                reverse=True
            )[:20]

            # Format as list of dicts with rank
            footprint = []
            for rank, (price, volume) in enumerate(sorted_bins, start=1):
                # Note: We don't track buy/sell split per price bin in incremental mode
                # This would require more state. For now, just report total volume.
                footprint.append({
                    "price": price,
                    "volume": volume,
                    "buy_vol": 0.0,  # Not tracked in incremental mode
                    "sell_vol": 0.0,  # Not tracked in incremental mode
                    "rank": rank,
                })

            return footprint
        except Exception as exc:
            logger.exception("Error calculating footprint: %s", exc)
            return []

    def get_latest_metrics(self) -> Optional[Dict[str, Any]]:
        """Get the latest calculated metrics.

        Returns:
            Latest metrics dict or None if no metrics have been calculated
        """
        return self._latest_metrics

    def get_metrics_with_metadata(self) -> Dict[str, Any]:
        """Get metrics with additional metadata.

        Returns:
            Dict with 'metrics' and 'metadata' (last_update, trade_count)
        """
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
        """Initialize cumulative state from backfilled trades.

        Args:
            trades: List of backfilled TradeTick objects
        """
        logger.info("Initializing OrderFlowAnalyzer from backfill with %d trades", len(trades))

        for trade in trades:
            price = float(trade.price)
            qty = float(trade.qty)
            is_buyer_maker = trade.isBuyerMaker

            # Update cumulative state
            self._sum_price_qty += price * qty
            self._sum_qty += qty

            price_bin = self._bin_price(price)
            self._volume_by_price[price_bin] += qty

            if is_buyer_maker:
                self._sell_volume += qty
            else:
                self._buy_volume += qty

            self._trade_count += 1

        # Calculate initial metrics
        self._update_metrics()

        logger.info(
            "Backfill initialization complete: VWAP=%.2f, POC=%.2f, Trades=%d",
            self._latest_metrics.get("vwap") if self._latest_metrics else None,
            self._latest_metrics.get("poc") if self._latest_metrics else None,
            self._trade_count,
        )

    def initialize_from_state(
        self,
        sum_price_qty: float,
        sum_qty: float,
        volume_by_price: Dict[float, float],
        buy_volume: float,
        sell_volume: float,
        trade_count: int,
    ) -> None:
        """Initialize cumulative state from pre-calculated values.

        Args:
            sum_price_qty: Cumulative sum of price * qty
            sum_qty: Cumulative sum of qty
            volume_by_price: Dict mapping price bins to volumes
            buy_volume: Total buy volume
            sell_volume: Total sell volume
            trade_count: Number of trades
        """
        logger.info("Initializing OrderFlowAnalyzer from state (trades=%d)", trade_count)

        self._sum_price_qty = sum_price_qty
        self._sum_qty = sum_qty
        self._volume_by_price = defaultdict(float, volume_by_price)
        self._buy_volume = buy_volume
        self._sell_volume = sell_volume
        self._trade_count = trade_count

        # Calculate initial metrics
        self._update_metrics()

        logger.info(
            "State initialization complete: VWAP=%.2f, POC=%.2f",
            self._latest_metrics.get("vwap") if self._latest_metrics else None,
            self._latest_metrics.get("poc") if self._latest_metrics else None,
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


# Global instance
_analyzer_instance: Optional[OrderFlowAnalyzer] = None


def get_orderflow_analyzer() -> OrderFlowAnalyzer:
    """Get the global OrderFlowAnalyzer instance."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = OrderFlowAnalyzer()
    return _analyzer_instance
