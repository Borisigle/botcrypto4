"""Context analyzer for market regime detection."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.context.service import get_context_service

from ..models import ContextAnalysis, MarketRegime


class ContextAnalyzer:
    """Analyzes market context to detect range vs trend regimes."""

    def __init__(self, context_service=None) -> None:
        self.context_service = context_service or get_context_service()
        self.logger = logging.getLogger("context_analyzer")

        # Analysis parameters
        self.range_threshold = 0.5  # Threshold for range vs trend classification
        self.min_volume_threshold = 100.0  # Minimum volume for reliable analysis

    def analyze(self) -> Optional[ContextAnalysis]:
        """Perform context analysis and return regime classification."""
        try:
            # Get current context data
            context_payload = self.context_service.context_payload()
            levels_payload = self.context_service.levels_payload()
            stats_payload = self.context_service.stats_payload()

            # Extract key metrics
            vwap = levels_payload.get("VWAP")
            poc = levels_payload.get("POCd")
            cumulative_delta = stats_payload.get("cd_pre", 0.0)

            # Get volume profile data
            debug_poc = self.context_service.debug_poc_payload()
            top_bins = debug_poc.get("top_bins", [])
            total_volume = sum(bin_data.get("volume", 0) for bin_data in top_bins)

            # Perform regime analysis
            regime, confidence = self._classify_regime(
                vwap, poc, cumulative_delta, total_volume, context_payload, levels_payload
            )

            # Calculate volume profile strength
            volume_profile_strength = self._calculate_volume_profile_strength(top_bins, total_volume)

            return ContextAnalysis(
                regime=regime,
                confidence=confidence,
                vwap=vwap,
                poc=poc,
                cumulative_delta=cumulative_delta,
                volume_profile_strength=volume_profile_strength,
                timestamp=datetime.now(timezone.utc),
            )

        except Exception as exc:
            self.logger.exception("Error in context analysis: %s", exc)
            return None

    def _classify_regime(
        self,
        vwap: Optional[float],
        poc: Optional[float],
        cumulative_delta: float,
        total_volume: float,
        context_payload: dict,
        levels_payload: dict,
    ) -> tuple[MarketRegime, float]:
        """Classify market regime based on context metrics."""
        confidence = 0.0
        regime = MarketRegime.RANGE  # Default to range

        if total_volume < self.min_volume_threshold:
            # Insufficient data for reliable classification
            return MarketRegime.RANGE, 0.3

        # Factor 1: VWAP vs POC relationship
        vwap_poc_factor = 0.0
        if vwap is not None and poc is not None:
            vwap_poc_distance = abs(vwap - poc) / poc if poc != 0 else 0
            # Small distance suggests range, large distance suggests trend
            vwap_poc_factor = max(0, 1 - vwap_poc_distance * 100)  # Normalize
            confidence += 0.3

        # Factor 2: Cumulative delta strength
        delta_factor = 0.0
        if cumulative_delta != 0:
            # Strong delta suggests trend
            delta_strength = min(abs(cumulative_delta) / total_volume, 1.0)
            delta_factor = delta_strength
            confidence += 0.3

        # Factor 3: Volume distribution
        volume_factor = 0.0
        debug_poc = self.context_service.debug_poc_payload()
        top_bins = debug_poc.get("top_bins", [])
        if top_bins:
            # Calculate volume concentration
            top_3_volume = sum(bin_data.get("volume", 0) for bin_data in top_bins[:3])
            concentration = top_3_volume / total_volume if total_volume > 0 else 0
            # High concentration suggests range (trading around specific levels)
            volume_factor = concentration
            confidence += 0.2

        # Factor 4: Session state
        session_factor = 0.0
        session_data = context_payload.get("session", {})
        session_state = session_data.get("state", "off")
        if session_state == "london":
            session_factor = 0.6  # London tends to be more range-bound
        elif session_state == "overlap":
            session_factor = 0.4  # Overlap can have more trends
        confidence += 0.2

        # Combine factors
        trend_score = (delta_factor * 0.5 + (1 - vwap_poc_factor) * 0.3 + (1 - volume_factor) * 0.2)
        range_score = (vwap_poc_factor * 0.4 + volume_factor * 0.4 + session_factor * 0.2)

        # Normalize confidence
        confidence = min(confidence, 1.0)

        # Determine regime
        if trend_score > range_score + self.range_threshold:
            regime = MarketRegime.TREND
        else:
            regime = MarketRegime.RANGE

        return regime, confidence

    def _calculate_volume_profile_strength(self, top_bins: list, total_volume: float) -> Optional[float]:
        """Calculate the strength of the volume profile."""
        if not top_bins or total_volume <= 0:
            return None

        # Calculate Gini coefficient for volume distribution
        volumes = [bin_data.get("volume", 0) for bin_data in top_bins]
        volumes.sort()

        n = len(volumes)
        if n == 0:
            return None

        cumulative_volumes = []
        cumulative_sum = 0
        for volume in volumes:
            cumulative_sum += volume
            cumulative_volumes.append(cumulative_sum)

        # Calculate Gini coefficient
        sum_cumulative = sum(cumulative_volumes)
        if sum_cumulative == 0:
            return 0.0

        gini = (n + 1 - 2 * sum_cumulative / sum_cumulative) / n
        return max(0.0, min(1.0, gini))

    def get_diagnostics(self) -> dict:
        """Get detailed diagnostics for the context analysis."""
        try:
            analysis = self.analyze()
            if not analysis:
                return {"error": "Analysis failed"}

            # Get additional context data
            context_payload = self.context_service.context_payload()
            levels_payload = self.context_service.levels_payload()
            debug_poc = self.context_service.debug_poc_payload()

            return {
                "analysis": analysis.model_dump(),
                "context": context_payload,
                "levels": levels_payload,
                "volume_profile": debug_poc,
                "parameters": {
                    "range_threshold": self.range_threshold,
                    "min_volume_threshold": self.min_volume_threshold,
                },
            }
        except Exception as exc:
            self.logger.exception("Error getting diagnostics: %s", exc)
            return {"error": str(exc)}


# Global instance
_analyzer_instance: Optional[ContextAnalyzer] = None


def get_context_analyzer() -> ContextAnalyzer:
    """Get the global context analyzer instance."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = ContextAnalyzer()
    return _analyzer_instance