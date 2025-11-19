"""Indicator models for technical analysis."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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

