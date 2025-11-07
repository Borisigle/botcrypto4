"""Shared price binning utility for precise tick-size based quantization."""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_FLOOR, getcontext
from typing import Optional

logger = logging.getLogger(__name__)

# Set high precision for Decimal calculations
getcontext().prec = 28


class PriceBinningError(Exception):
    """Raised when price binning configuration is invalid."""
    pass


def quantize_price_to_tick(
    price: float,
    tick_size: Optional[float],
    fallback_tick_size: float = 0.1,
    symbol: str = "UNKNOWN",
) -> float:
    """
    Quantize a price to the nearest tick-size bin using precise Decimal math.
    
    This function eliminates floating-point drift by using Decimal arithmetic
    for all calculations. It implements floor rounding to match TradingView's
    volume profile behavior.
    
    Args:
        price: The raw price to quantize
        tick_size: The exchange tick size from exchangeInfo. If None or invalid,
                  uses fallback_tick_size
        fallback_tick_size: Default tick size when exchange info is unavailable.
                           Can be overridden via PROFILE_TICK_SIZE env var.
        symbol: Symbol name for logging purposes
    
    Returns:
        The quantized price as a float, snapped down to the nearest tick bin
        
    Raises:
        PriceBinningError: If tick_size or fallback_tick_size is invalid
        
    Examples:
        >>> quantize_price_to_tick(101.505, 0.1)
        101.5
        >>> quantize_price_to_tick(101.509, 0.1) 
        101.5
        >>> quantize_price_to_tick(101.501, 0.01)
        101.5
    """
    if not isinstance(price, (int, float)) or price <= 0:
        raise PriceBinningError(f"Invalid price: {price}")
    
    # Determine effective tick size - treat None, 0, or negative as invalid
    effective_tick = tick_size if tick_size and tick_size > 0 else fallback_tick_size
    
    if effective_tick <= 0:
        raise PriceBinningError(f"Invalid tick size: {effective_tick}")
    
    # Convert to Decimal for precise arithmetic
    try:
        price_dec = Decimal(str(price))
        tick_dec = Decimal(str(effective_tick))
    except Exception as exc:
        raise PriceBinningError(f"Decimal conversion failed: price={price}, tick={effective_tick}: {exc}")
    
    # Calculate bin index using floor division, then convert back to price
    try:
        bin_index = (price_dec / tick_dec).to_integral_value(rounding=ROUND_FLOOR)
        quantized_price = float(bin_index * tick_dec)
        return quantized_price
    except Exception as exc:
        raise PriceBinningError(f"Price quantization failed: price={price}, tick={effective_tick}: {exc}")


def get_effective_tick_size(
    exchange_tick_size: Optional[float],
    fallback_tick_size: float,
    symbol: str = "UNKNOWN",
) -> tuple[float, bool]:
    """
    Get the effective tick size, logging any fallback usage.
    
    Args:
        exchange_tick_size: Tick size from exchange info
        fallback_tick_size: Configured fallback tick size
        symbol: Symbol name for logging
        
    Returns:
        Tuple of (effective_tick_size, used_exchange_info)
    """
    if exchange_tick_size and exchange_tick_size > 0:
        logger.debug(
            "price_binning_using_exchange symbol=%s tickSize=%s",
            symbol,
            exchange_tick_size,
        )
        return exchange_tick_size, True
    
    logger.warning(
        "price_binning_falling_back symbol=%s exchangeTickSize=%s fallbackTickSize=%s",
        symbol,
        exchange_tick_size,
        fallback_tick_size,
    )
    return fallback_tick_size, False


def validate_tick_size(tick_size: float, symbol: str = "UNKNOWN") -> None:
    """
    Validate that a tick size is reasonable for typical crypto markets.
    
    Args:
        tick_size: The tick size to validate
        symbol: Symbol name for logging
        
    Raises:
        PriceBinningError: If tick size is invalid
    """
    if not isinstance(tick_size, (int, float)) or tick_size <= 0:
        raise PriceBinningError(f"Invalid tick size for {symbol}: {tick_size}")
    
    # Check for common crypto tick sizes (allow some flexibility)
    common_ticks = [0.001, 0.01, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
    
    # Allow tick sizes that are powers of 10 or common fractions
    is_reasonable = (
        tick_size in common_ticks or
        (tick_size > 0 and tick_size <= 100 and 
         (tick_size.as_integer_ratio()[1] in [1, 2, 4, 5, 8, 10, 20, 25, 40, 50, 100]))
    )
    
    if not is_reasonable:
        logger.warning(
            "price_binning_unusual_tick_size symbol=%s tickSize=%s",
            symbol,
            tick_size,
        )