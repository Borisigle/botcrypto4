"""High-performance metrics calculation using vectorized operations."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import pandas_ta
import polars as pl

logger = logging.getLogger("metrics_calculator")


class MetricsCalculator:
    """Calculate market metrics (VWAP, POC, Delta, Footprint) from trade data."""

    def __init__(self, tick_size: float = 0.1):
        """Initialize MetricsCalculator with tick size for price binning.

        Args:
            tick_size: Price tick size for binning (default 0.1)
        """
        self.tick_size = tick_size

    def calculate(self, trades_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate market metrics from trade data.

        Args:
            trades_list: List of trades with format:
                [{
                    'price': float,
                    'qty': float,
                    'is_buyer_maker': bool,
                    'timestamp': float (unix epoch)
                }, ...]

        Returns:
            Dict with keys:
                - vwap: Volume-weighted average price
                - poc: Point of control (price level with highest volume)
                - delta: Cumulative buy volume - sell volume
                - buy_volume: Total buy volume
                - sell_volume: Total sell volume
                - footprint: List of top 20 price bins sorted by volume
                - trade_count: Number of trades
        """
        if not trades_list:
            return {
                "vwap": None,
                "poc": None,
                "delta": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "footprint": [],
                "trade_count": 0,
            }

        # Convert to Polars DataFrame for efficient processing
        df = pl.DataFrame(trades_list)

        # Calculate metrics
        vwap = self._vwap(df)
        poc = self._poc(df)
        delta = self._delta(df)
        buy_volume = self._buy_volume(df)
        sell_volume = self._sell_volume(df)
        footprint = self._footprint(df)

        return {
            "vwap": vwap,
            "poc": poc,
            "delta": delta,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "footprint": footprint,
            "trade_count": len(trades_list),
        }

    def _vwap(self, df: pl.DataFrame) -> Optional[float]:
        """Calculate VWAP (Volume-Weighted Average Price) using pandas_ta.

        VWAP = Σ(price * volume) / Σ(volume)

        Args:
            df: Polars DataFrame with 'price' and 'qty' columns

        Returns:
            VWAP value or None if no data
        """
        if df.is_empty():
            return None

        try:
            # Convert to pandas for pandas_ta compatibility
            pdf = df.to_pandas()

            # Create required columns for pandas_ta.vwap
            # Need 'high', 'low', 'close', 'volume'
            pdf["high"] = pdf["price"]
            pdf["low"] = pdf["price"]
            pdf["close"] = pdf["price"]
            pdf["volume"] = pdf["qty"]

            # Calculate VWAP (anchor at 00:00 UTC)
            vwap_series = pandas_ta.vwap(
                high=pdf["high"],
                low=pdf["low"],
                close=pdf["close"],
                volume=pdf["volume"],
            )

            # Get the last valid VWAP value
            if vwap_series is not None and len(vwap_series) > 0:
                vwap_value = float(vwap_series.iloc[-1])
                if not pd.isna(vwap_value):
                    return vwap_value

            # Fallback: simple average if VWAP calculation fails
            return float(
                (pdf["price"] * pdf["qty"]).sum() / pdf["qty"].sum()
            )
        except Exception as e:
            logger.exception("Error calculating VWAP: %s", e)
            # Fallback to simple weighted average
            try:
                return float(
                    (df["price"] * df["qty"]).sum() / df["qty"].sum()
                )
            except Exception as fallback_e:
                logger.exception("Error in VWAP fallback: %s", fallback_e)
                return None

    def _poc(self, df: pl.DataFrame) -> Optional[float]:
        """Calculate POC (Point of Control) - price with highest volume.

        Args:
            df: Polars DataFrame with 'price' and 'qty' columns

        Returns:
            Price of the bin with maximum volume or None
        """
        if df.is_empty():
            return None

        try:
            # Bin prices by tick_size
            df_binned = df.with_columns(
                pl.col("price")
                .truediv(self.tick_size)
                .cast(pl.Int64)
                .mul(self.tick_size)
                .alias("price_bin")
            )

            # Group by binned price and sum volume
            volume_by_bin = (
                df_binned.group_by("price_bin")
                .agg(pl.col("qty").sum().alias("total_volume"))
                .sort("total_volume", descending=True)
            )

            if volume_by_bin.is_empty():
                return None

            # Return the price of the bin with max volume
            poc_price = float(volume_by_bin[0, "price_bin"])
            return poc_price
        except Exception as e:
            logger.exception("Error calculating POC: %s", e)
            return None

    def _delta(self, df: pl.DataFrame) -> float:
        """Calculate cumulative delta (buy volume - sell volume).

        Args:
            df: Polars DataFrame with 'qty' and 'is_buyer_maker' columns

        Returns:
            Cumulative delta value
        """
        try:
            buy_vol = df.filter(~pl.col("is_buyer_maker"))["qty"].sum()
            sell_vol = df.filter(pl.col("is_buyer_maker"))["qty"].sum()
            return float(buy_vol - sell_vol)
        except Exception as e:
            logger.exception("Error calculating delta: %s", e)
            return 0.0

    def _buy_volume(self, df: pl.DataFrame) -> float:
        """Calculate total buy volume (is_buyer_maker == False).

        Args:
            df: Polars DataFrame with 'qty' and 'is_buyer_maker' columns

        Returns:
            Total buy volume
        """
        try:
            return float(df.filter(~pl.col("is_buyer_maker"))["qty"].sum())
        except Exception as e:
            logger.exception("Error calculating buy volume: %s", e)
            return 0.0

    def _sell_volume(self, df: pl.DataFrame) -> float:
        """Calculate total sell volume (is_buyer_maker == True).

        Args:
            df: Polars DataFrame with 'qty' and 'is_buyer_maker' columns

        Returns:
            Total sell volume
        """
        try:
            return float(df.filter(pl.col("is_buyer_maker"))["qty"].sum())
        except Exception as e:
            logger.exception("Error calculating sell volume: %s", e)
            return 0.0

    def _footprint(self, df: pl.DataFrame) -> List[Dict[str, Any]]:
        """Calculate volume profile footprint (top 20 price bins by volume).

        Args:
            df: Polars DataFrame with 'price', 'qty', 'is_buyer_maker' columns

        Returns:
            List of top 20 price bins with structure:
                [{
                    'price': float,
                    'volume': float,
                    'buy_vol': float,
                    'sell_vol': float,
                    'rank': int
                }, ...]
        """
        if df.is_empty():
            return []

        try:
            # Bin prices by tick_size
            df_binned = df.with_columns(
                pl.col("price")
                .truediv(self.tick_size)
                .cast(pl.Int64)
                .mul(self.tick_size)
                .alias("price_bin")
            )

            # Group by binned price and calculate volume, buy vol, sell vol
            footprint_df = (
                df_binned.group_by("price_bin")
                .agg([
                    pl.col("qty").sum().alias("volume"),
                    pl.when(~pl.col("is_buyer_maker"))
                    .then(pl.col("qty"))
                    .otherwise(0)
                    .sum()
                    .alias("buy_vol"),
                    pl.when(pl.col("is_buyer_maker"))
                    .then(pl.col("qty"))
                    .otherwise(0)
                    .sum()
                    .alias("sell_vol"),
                ])
                .sort("volume", descending=True)
                .head(20)
            )

            # Add rank
            footprint_df = footprint_df.with_row_index("rank").with_columns(
                pl.col("rank").add(1)
            )

            # Convert to list of dicts
            result = []
            for row in footprint_df.iter_rows(named=True):
                result.append({
                    "price": float(row["price_bin"]),
                    "volume": float(row["volume"]),
                    "buy_vol": float(row["buy_vol"]),
                    "sell_vol": float(row["sell_vol"]),
                    "rank": int(row["rank"]),
                })

            return result
        except Exception as e:
            logger.exception("Error calculating footprint: %s", e)
            return []
