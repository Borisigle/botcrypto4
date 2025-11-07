"""Order flow analyzer for real-time market metrics calculation."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
    ) -> None:
        """Initialize OrderFlowAnalyzer.

        Args:
            settings: Application settings
            metrics_calculator: MetricsCalculator instance (optional)
            calculation_interval: Number of trades before recalculating metrics (default 50)
        """
        self.settings = settings or get_settings()
        self.metrics_calculator = metrics_calculator or MetricsCalculator(
            tick_size=0.01  # Default for BTCUSDT
        )
        self.calculation_interval = calculation_interval

        # Trade buffer for current window
        self._trades_buffer: list[Dict[str, Any]] = []
        self._trade_count: int = 0

        # Latest metrics
        self._latest_metrics: Optional[Dict[str, Any]] = None
        self._last_metrics_update: Optional[datetime] = None

    def ingest_trade(self, trade: TradeTick) -> None:
        """Ingest a trade tick and optionally update metrics.

        Args:
            trade: TradeTick from websocket stream
        """
        # Convert TradeTick to dict format expected by MetricsCalculator
        trade_dict = {
            "price": float(trade.price),
            "qty": float(trade.qty),
            "is_buyer_maker": trade.isBuyerMaker,
            "timestamp": trade.ts.timestamp(),
        }

        self._trades_buffer.append(trade_dict)
        self._trade_count += 1

        # Calculate metrics every N trades
        if self._trade_count % self.calculation_interval == 0:
            self._update_metrics()

    def _update_metrics(self) -> None:
        """Recalculate metrics from current trades buffer."""
        if not self._trades_buffer:
            return

        try:
            metrics = self.metrics_calculator.calculate(self._trades_buffer)
            self._latest_metrics = metrics
            self._last_metrics_update = datetime.now(timezone.utc)

            # Log the update
            if metrics.get("vwap") is not None and metrics.get("poc") is not None:
                logger.info(
                    "Metrics updated: VWAP=%.2f, POC=%.2f, Delta=%.2f",
                    metrics["vwap"],
                    metrics["poc"],
                    metrics["delta"],
                )
        except Exception as exc:
            logger.exception("Error updating metrics: %s", exc)

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
                "buffer_size": len(self._trades_buffer),
            },
        }

    def reset_buffer(self) -> None:
        """Reset the trades buffer (e.g., on day boundary)."""
        self._trades_buffer.clear()
        self._trade_count = 0


# Global instance
_analyzer_instance: Optional[OrderFlowAnalyzer] = None


def get_orderflow_analyzer() -> OrderFlowAnalyzer:
    """Get the global OrderFlowAnalyzer instance."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = OrderFlowAnalyzer()
    return _analyzer_instance
