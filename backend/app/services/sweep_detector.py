"""Sweep Detector service for confluence-based trading signals."""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, List, Optional

from app.models.indicators import Signal

logger = logging.getLogger("sweep_detector")


class SweepDetector:
    """Detects trading setups based on CVD divergence, Volume Delta spike, and liquidations."""

    def __init__(self):
        self.signals: Deque[Signal] = deque(maxlen=100)
        self.cvd_history: Deque[dict] = deque(maxlen=1000)
        self.vol_delta_history: Deque[dict] = deque(maxlen=1000)
        self.last_signal_time = datetime.now(timezone.utc)
        self._lock = Lock()

    async def analyze(
        self,
        current_price: float,
        cvd_snapshot: dict,
        vol_delta_snapshot: dict,
        liquidation_support: Optional[float] = None,
        liquidation_resistance: Optional[float] = None,
    ) -> Optional[Signal]:
        """Analyzes confluence and generates signal if setup is detected.
        
        Args:
            current_price: Current market price
            cvd_snapshot: CVD data dict with 'cvd' key
            vol_delta_snapshot: Volume Delta data dict with 'volume_delta' key
            liquidation_support: Optional support level from liquidation clusters
            liquidation_resistance: Optional resistance level from liquidation clusters
        
        Returns:
            Signal if setup is detected, None otherwise
        """
        with self._lock:
            # Store historical data
            self.cvd_history.append({
                "time": datetime.now(timezone.utc),
                "cvd": cvd_snapshot.get("cvd", 0),
                "price": current_price,
            })
            self.vol_delta_history.append({
                "time": datetime.now(timezone.utc),
                "volume_delta": vol_delta_snapshot.get("volume_delta", 0),
            })

            # 1. Detect CVD divergence
            cvd_divergence = self._detect_cvd_divergence(current_price)
            if not cvd_divergence:
                return None

            # 2. Detect Volume Delta spike
            vol_delta_spike = self._detect_volume_delta_spike(
                vol_delta_snapshot.get("volume_delta", 0)
            )
            if not vol_delta_spike:
                return None

            # 3. Generate signal
            signal = self._generate_signal(
                current_price,
                cvd_snapshot,
                vol_delta_snapshot,
                liquidation_support,
                liquidation_resistance,
                cvd_divergence,
                vol_delta_spike,
            )

            # 4. Store signal
            self.signals.append(signal)
            self.last_signal_time = datetime.now(timezone.utc)

            logger.info(
                f"SIGNAL GENERATED: {signal.setup_type} at {signal.entry_price}, "
                f"RR: {signal.risk_reward:.2f}, Score: {signal.confluence_score}"
            )

        return signal

    def _detect_cvd_divergence(self, current_price: float) -> bool:
        """Detects CVD divergence (price down, CVD up = bullish).
        
        Returns True if bullish divergence is detected.
        """
        if len(self.cvd_history) < 20:
            return False

        recent_cvd = list(self.cvd_history)[-20:]

        # Bullish: price down, CVD up
        price_downtrend = recent_cvd[-1]["price"] < recent_cvd[-10]["price"]
        cvd_uptrend = recent_cvd[-1]["cvd"] > recent_cvd[-10]["cvd"]

        bullish = price_downtrend and cvd_uptrend

        return bullish

    def _detect_volume_delta_spike(self, current_vol_delta: float) -> bool:
        """Detects Volume Delta spike (current > 1.5x average).
        
        Returns True if spike is detected.
        """
        if len(self.vol_delta_history) < 20:
            return False

        recent_deltas = [
            abs(v["volume_delta"]) for v in list(self.vol_delta_history)[-20:]
        ]
        avg_delta = sum(recent_deltas[:-1]) / len(recent_deltas[:-1]) if len(recent_deltas) > 1 else 1
        current_delta = abs(current_vol_delta)

        # Spike: current > 1.5x average
        spike = current_delta > (avg_delta * 1.5)

        return spike

    def _calculate_volume_delta_percentile(self) -> float:
        """Calculate volume delta percentile vs historical.
        
        Returns a percentile value (0-100).
        """
        if len(self.vol_delta_history) < 2:
            return 50.0

        deltas = [
            abs(v["volume_delta"]) for v in list(self.vol_delta_history)
        ]
        if not deltas:
            return 50.0

        current_delta = abs(deltas[-1]) if deltas else 0
        count_below = sum(1 for d in deltas[:-1] if d <= current_delta)
        percentile = (count_below / len(deltas[:-1])) * 100 if len(deltas) > 1 else 50.0

        return percentile

    def _generate_signal(
        self,
        current_price: float,
        cvd_snapshot: dict,
        vol_delta_snapshot: dict,
        liquidation_support: Optional[float],
        liquidation_resistance: Optional[float],
        cvd_divergence: bool,
        vol_delta_spike: bool,
    ) -> Signal:
        """Generate a trading signal with entry, SL, TP, and RR."""

        # Entry = current price
        entry = current_price

        # SL = liquidation support (if exists), else -1%
        if liquidation_support:
            sl = liquidation_support * 0.995  # 0.5% below
        else:
            sl = current_price * 0.99  # 1% below

        # TP = liquidation resistance (if exists), else +3%
        if liquidation_resistance:
            tp = liquidation_resistance * 1.005  # 0.5% above
        else:
            tp = current_price * 1.03  # 3% above

        # Calculate RR
        risk = entry - sl
        reward = tp - entry
        rr = reward / risk if risk > 0 else 0

        # Confluence score (0-100)
        score = 50  # Base
        if cvd_divergence:
            score += 25
        if vol_delta_spike:
            score += 25
        if liquidation_support and liquidation_resistance:
            if liquidation_support < entry < liquidation_resistance:
                score += 10
        score = min(score, 100)

        reason = "CVD divergence + Volume Delta spike"
        if liquidation_support:
            reason += f" + Liquidation support at {liquidation_support:.2f}"

        percentile = self._calculate_volume_delta_percentile()

        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            setup_type="bullish_sweep",
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            risk_reward=rr,
            confluence_score=score,
            cvd_value=cvd_snapshot.get("cvd", 0),
            cvd_divergence=cvd_divergence,
            volume_delta=vol_delta_snapshot.get("volume_delta", 0),
            volume_delta_percentile=percentile,
            liquidation_support=liquidation_support,
            liquidation_resistance=liquidation_resistance,
            reason=reason,
        )

        return signal

    def get_last_signal(self) -> Optional[Signal]:
        """Return the last generated signal."""
        with self._lock:
            return self.signals[-1] if self.signals else None

    def get_signals_history(self, limit: int = 50) -> List[Signal]:
        """Return signal history (up to limit)."""
        with self._lock:
            return list(self.signals)[-limit:]


_sweep_detector: Optional[SweepDetector] = None


def init_sweep_detector() -> SweepDetector:
    global _sweep_detector
    if _sweep_detector is None:
        _sweep_detector = SweepDetector()
    return _sweep_detector


def get_sweep_detector() -> SweepDetector:
    if _sweep_detector is None:
        raise RuntimeError("SweepDetector has not been initialized yet")
    return _sweep_detector
