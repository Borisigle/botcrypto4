"""Indicator models for technical analysis."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class Signal(BaseModel):
    """Trading signal generated from confluence analysis."""
    
    timestamp: datetime
    setup_type: str  # "bullish_sweep" or "bearish_sweep"
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float  # TP - Entry / Entry - SL
    confluence_score: float  # 0-100 (how strong is the setup)
    
    # Details
    cvd_value: float
    cvd_divergence: bool
    volume_delta: float
    volume_delta_percentile: float  # vs historical
    liquidation_support: Optional[float] = None
    liquidation_resistance: Optional[float] = None
    
    reason: str  # Explanation for entry
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class VolumeDeltaSnapshot(BaseModel):
    """Snapshot of Volume Delta for a specific time period."""
    
    period: int  # seconds
    buy_volume: float
    sell_volume: float
    volume_delta: float  # buy - sell
    trade_count: int
    timestamp: datetime
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class CVDSnapshot(BaseModel):
    """Snapshot of Cumulative Volume Delta at a point in time."""
    
    cvd: float
    reset_time: datetime
    seconds_since_reset: float
    buy_volume: float  # total buy volume since reset
    sell_volume: float  # total sell volume since reset
    volume_delta: float  # buy_volume - sell_volume (= CVD)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class LiquidationCluster(BaseModel):
    """Aggregated liquidation data for a specific price level."""
    
    price_level: float
    buy_liquidations: float
    sell_liquidations: float
    total_liquidations: float
    ratio: float  # buy / sell


class LiquidationSnapshot(BaseModel):
    """Snapshot of liquidation clusters plus support/resistance levels."""
    
    clusters: List[LiquidationCluster]
    nearest_support: Optional[float]
    nearest_resistance: Optional[float]
    timestamp: datetime
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

