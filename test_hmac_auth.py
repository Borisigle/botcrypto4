#!/usr/bin/env python3
"""
Test script for HMAC authentication with Binance API.

This script demonstrates the test mode functionality for validating
HMAC-SHA256 signing before expanding to full backfill.

Usage:
    # Test with API keys (recommended)
    export BINANCE_API_KEY=your_api_key_here
    export BINANCE_API_SECRET=your_api_secret_here
    export CONTEXT_BACKFILL_TEST_MODE=true
    python test_hmac_auth.py

    # Test without API keys (public endpoints)
    unset BINANCE_API_KEY
    unset BINANCE_API_SECRET
    export CONTEXT_BACKFILL_TEST_MODE=true
    python test_hmac_auth.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

# Add the backend directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from app.context.backfill import BinanceTradeHistory
from app.ws.models import Settings, get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_hmac_authentication():
    """Test HMAC authentication with single 1-hour window."""
    logger.info("=== HMAC AUTHENTICATION TEST ===")
    
    # Load settings
    settings = get_settings()
    
    # Enable test mode
    settings.context_backfill_test_mode = True
    
    # Show configuration
    logger.info(f"Symbol: {settings.symbol}")
    logger.info(f"REST Base URL: {settings.rest_base_url}")
    logger.info(f"Test Mode: {settings.context_backfill_test_mode}")
    
    if settings.binance_api_key:
        key_preview = f"{settings.binance_api_key[:4]}...{settings.binance_api_key[-4:]}" if len(settings.binance_api_key) > 8 else "***"
        logger.info(f"API Key: {key_preview}")
        logger.info(f"API Secret: {'*' * len(settings.binance_api_secret) if settings.binance_api_secret else 'None'}")
    else:
        logger.warning("No API credentials configured - using public endpoints")
    
    try:
        # Create backfill instance
        history = BinanceTradeHistory(settings)
        
        # Run the test
        logger.info("Starting authentication test...")
        trades = await history.test_single_window()
        
        # Results
        logger.info(f"✅ Test completed successfully!")
        logger.info(f"   Trades loaded: {len(trades)}")
        
        if trades:
            # Calculate some basic metrics
            total_qty = sum(trade.qty for trade in trades)
            total_volume = sum(trade.price * trade.qty for trade in trades)
            avg_price = total_volume / total_qty if total_qty > 0 else 0
            min_price = min(trade.price for trade in trades)
            max_price = max(trade.price for trade in trades)
            
            logger.info(f"   Price range: ${min_price:.2f} - ${max_price:.2f}")
            logger.info(f"   Average price: ${avg_price:.2f}")
            logger.info(f"   Total volume: {total_qty:.6f} BTC")
            
            # Show first few trades for verification
            logger.info("   Sample trades:")
            for i, trade in enumerate(trades[:3]):
                logger.info(f"     {i+1}. {trade.ts.isoformat()} - ${trade.price:.2f} x {trade.qty:.6f}")
        
        logger.info("🎉 HMAC authentication is working correctly!")
        logger.info("   Ready to expand to full backfill (set CONTEXT_BACKFILL_TEST_MODE=false)")
        
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        logger.error("   Check your API credentials and network connection")
        logger.error("   For API key issues, verify:")
        logger.error("     - BINANCE_API_KEY is correct")
        logger.error("     - BINANCE_API_SECRET is correct")
        logger.error("     - API key has 'Read' permissions for futures trading")
        return False
    
    finally:
        # Cleanup
        if 'history' in locals():
            await history.http_client.close()
    
    return True


if __name__ == "__main__":
    # Check environment
    logger.info("Environment check:")
    logger.info(f"  BINANCE_API_KEY: {'Set' if os.getenv('BINANCE_API_KEY') else 'Not set'}")
    logger.info(f"  BINANCE_API_SECRET: {'Set' if os.getenv('BINANCE_API_SECRET') else 'Not set'}")
    logger.info(f"  CONTEXT_BACKFILL_TEST_MODE: {os.getenv('CONTEXT_BACKFILL_TEST_MODE', 'false')}")
    
    # Run the test
    success = asyncio.run(test_hmac_authentication())
    
    if success:
        logger.info("\n✅ All tests passed!")
        sys.exit(0)
    else:
        logger.error("\n❌ Tests failed!")
        sys.exit(1)