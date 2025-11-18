"""Trade model for Bybit WebSocket data."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class Trade(BaseModel):
    """Trade model representing a single trade."""
    
    price: float
    qty: float
    side: str  # "Buy" or "Sell"
    time: datetime
    symbol: str  # "BTCUSDT"
    trade_id: Optional[str] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }