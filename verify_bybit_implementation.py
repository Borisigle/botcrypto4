#!/usr/bin/env python3
"""
Verification script for Bybit backfill implementation.
Tests basic functionality without requiring external dependencies.
"""

import sys
import os
from datetime import datetime, timezone

# Add the backend directory to the path
sys.path.insert(0, '/home/engine/project/backend')

def test_imports():
    """Test that all imports work correctly."""
    try:
        from app.context.backfill import BybitConnectorHistory, BybitHttpClient, TradeHistoryProvider
        from app.ws.models import Settings
        print("✅ All imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False

def test_settings_initialization():
    """Test Settings with Bybit configuration."""
    try:
        settings = Settings(
            data_source="bybit",
            bybit_api_key="test_key",
            bybit_api_secret="test_secret",
            bybit_api_timeout=30,
            bybit_backfill_max_retries=5,
            bybit_backfill_retry_base=0.5,
            bybit_backfill_rate_limit_threshold=3,
            bybit_backfill_cooldown_seconds=60,
            bybit_backfill_public_delay_ms=50,
            bybit_backfill_max_concurrent_chunks=8,
        )
        
        # Verify settings are properly set
        assert settings.data_source == "bybit"
        assert settings.bybit_api_key == "test_key"
        assert settings.bybit_api_secret == "test_secret"
        assert settings.bybit_api_timeout == 30
        assert settings.bybit_backfill_max_concurrent_chunks == 8
        
        print("✅ Settings initialization successful")
        return True
    except Exception as e:
        print(f"❌ Settings error: {e}")
        return False

def test_bybit_client_creation():
    """Test BybitHttpClient creation."""
    try:
        from app.context.backfill import BybitHttpClient
        
        settings = Settings(
            bybit_api_key="test_key",
            bybit_api_secret="test_secret",
        )
        
        client = BybitHttpClient(settings)
        
        # Test authentication state
        assert client.use_auth is True
        assert "X-BAPI-API-KEY" in client.headers
        assert client.headers["X-BAPI-API-KEY"] == "test_key"
        
        # Test without auth
        settings_no_auth = Settings(bybit_api_key=None, bybit_api_secret=None)
        client_no_auth = BybitHttpClient(settings_no_auth)
        assert client_no_auth.use_auth is False
        assert "X-BAPI-API-KEY" not in client_no_auth.headers
        
        print("✅ BybitHttpClient creation successful")
        return True
    except Exception as e:
        print(f"❌ BybitHttpClient error: {e}")
        return False

def test_bybit_history_creation():
    """Test BybitConnectorHistory creation."""
    try:
        from app.context.backfill import BybitConnectorHistory
        
        # Test with auth
        settings_auth = Settings(
            bybit_api_key="test_key",
            bybit_api_secret="test_secret",
            bybit_backfill_max_concurrent_chunks=8,
            bybit_backfill_public_delay_ms=50,
        )
        history_auth = BybitConnectorHistory(settings_auth)
        
        assert history_auth.max_concurrent_chunks == 8  # Full concurrency with auth
        assert history_auth.request_delay == 0.0  # No delay with auth
        
        # Test without auth
        settings_no_auth = Settings(
            bybit_api_key=None,
            bybit_api_secret=None,
            bybit_backfill_max_concurrent_chunks=8,
            bybit_backfill_public_delay_ms=50,
        )
        history_no_auth = BybitConnectorHistory(settings_no_auth)
        
        assert history_no_auth.max_concurrent_chunks == 4  # Half concurrency for public
        assert history_no_auth.request_delay == 0.05  # 50ms delay
        
        print("✅ BybitConnectorHistory creation successful")
        return True
    except Exception as e:
        print(f"❌ BybitConnectorHistory error: {e}")
        return False

def test_trade_parsing():
    """Test Bybit trade parsing."""
    try:
        from app.context.backfill import BybitConnectorHistory
        from app.ws.models import TradeTick
        
        settings = Settings()
        history = BybitConnectorHistory(settings)
        
        # Test public trade format
        public_trade = {
            "execId": "test_trade_123",
            "symbol": "BTCUSDT",
            "price": "50000.0",
            "size": "0.1",
            "side": "Buy",
            "time": "1640995200000",
            "isBlockTrade": False
        }
        
        trade = history._parse_bybit_trade(public_trade)
        
        assert isinstance(trade, TradeTick)
        assert trade.price == 50000.0
        assert trade.qty == 0.1
        assert trade.side == "buy"
        assert trade.isBuyerMaker is False  # Buy side = taker
        assert trade.ts == datetime.fromtimestamp(1640995200000 / 1000, tz=timezone.utc)
        
        # Test private trade format
        private_trade = {
            "symbol": "BTCUSDT",
            "execId": "test_private_456",
            "side": "Sell",
            "execPrice": "50100.0",
            "execQty": "0.2",
            "execTime": "1640995300000"
        }
        
        trade_private = history._parse_bybit_trade(private_trade)
        
        assert trade_private.price == 50100.0
        assert trade_private.qty == 0.2
        assert trade_private.side == "sell"
        assert trade_private.isBuyerMaker is True  # Sell side = maker
        
        print("✅ Trade parsing successful")
        return True
    except Exception as e:
        print(f"❌ Trade parsing error: {e}")
        return False

def test_cache_conversion():
    """Test cache conversion methods."""
    try:
        from app.context.backfill import BybitConnectorHistory
        from app.ws.models import TradeTick
        
        settings = Settings()
        history = BybitConnectorHistory(settings)
        
        # Create test trade
        trade = TradeTick(
            ts=datetime(2025, 1, 1, 12, 30, 0, tzinfo=timezone.utc),
            price=50000.0,
            qty=0.1,
            side="buy",
            isBuyerMaker=False,
            id=12345
        )
        
        # Convert to dict
        trade_dict = history._trade_tick_to_dict(trade)
        
        assert trade_dict["T"] == 1735732200000  # timestamp in ms
        assert trade_dict["i"] == "12345"  # ID as string
        assert trade_dict["p"] == 50000.0
        assert trade_dict["q"] == 0.1
        assert trade_dict["s"] == "buy"
        assert trade_dict["m"] is False
        
        # Convert back to TradeTick
        restored_trades = history._dicts_to_trade_ticks([trade_dict])
        restored_trade = restored_trades[0]
        
        assert restored_trade.price == trade.price
        assert restored_trade.qty == trade.qty
        assert restored_trade.side == trade.side
        assert restored_trade.isBuyerMaker == trade.isBuyerMaker
        assert restored_trade.id == trade.id
        
        print("✅ Cache conversion successful")
        return True
    except Exception as e:
        print(f"❌ Cache conversion error: {e}")
        return False

def test_context_service_integration():
    """Test ContextService integration with Bybit provider."""
    try:
        from app.context.service import ContextService
        from app.context.backfill import BybitConnectorHistory
        
        settings = Settings(data_source="bybit")
        context = ContextService(settings)
        
        # Test provider selection
        provider = context._get_history_provider()
        
        assert isinstance(provider, BybitConnectorHistory)
        assert provider.settings.data_source == "bybit"
        
        print("✅ ContextService integration successful")
        return True
    except Exception as e:
        print(f"❌ ContextService integration error: {e}")
        return False

def main():
    """Run all verification tests."""
    print("🔍 Verifying Bybit Backfill Implementation...")
    print("=" * 50)
    
    tests = [
        ("Imports", test_imports),
        ("Settings Initialization", test_settings_initialization),
        ("BybitHttpClient Creation", test_bybit_client_creation),
        ("BybitConnectorHistory Creation", test_bybit_history_creation),
        ("Trade Parsing", test_trade_parsing),
        ("Cache Conversion", test_cache_conversion),
        ("ContextService Integration", test_context_service_integration),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n📋 Testing {test_name}...")
        if test_func():
            passed += 1
        else:
            print(f"   ❌ {test_name} failed")
    
    print("\n" + "=" * 50)
    print(f"📊 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Bybit backfill implementation is working correctly.")
        print("\n📋 Key Features Verified:")
        print("  ✅ BybitConnectorHistory class implementation")
        print("  ✅ BybitHttpClient with authentication")
        print("  ✅ Trade field normalization (Bybit → TradeTick)")
        print("  ✅ Cache integration and conversion")
        print("  ✅ ContextService provider selection")
        print("  ✅ Configuration with new settings")
        print("\n🚀 Ready for production use!")
        return 0
    else:
        print(f"❌ {total - passed} tests failed. Please review implementation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())