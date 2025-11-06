#!/usr/bin/env python3
"""
Simple verification script for HMAC test mode implementation.
This script verifies the code structure and logic without requiring external dependencies.
"""

import os
import sys
from datetime import datetime, timezone

def test_settings_structure():
    """Test that Settings class has the new test_mode field."""
    print("Testing Settings structure...")
    
    # Read the models.py file
    with open('backend/app/ws/models.py', 'r') as f:
        content = f.read()
    
    # Check for test mode field
    if 'context_backfill_test_mode: bool = field(' in content:
        print("✅ context_backfill_test_mode field found in Settings")
    else:
        print("❌ context_backfill_test_mode field missing from Settings")
        return False
    
    # Check for _env_bool usage
    if '_env_bool("CONTEXT_BACKFILL_TEST_MODE", "false")' in content:
        print("✅ CONTEXT_BACKFILL_TEST_MODE environment variable properly configured")
    else:
        print("❌ CONTEXT_BACKFILL_TEST_MODE environment variable not properly configured")
        return False
    
    return True

def test_backfill_modifications():
    """Test that BinanceTradeHistory has test mode support."""
    print("\nTesting BinanceTradeHistory modifications...")
    
    # Read the backfill.py file
    with open('backend/app/context/backfill.py', 'r') as f:
        content = f.read()
    
    # Check for test mode attribute
    if 'self.test_mode = settings.context_backfill_test_mode' in content:
        print("✅ test_mode attribute properly set from settings")
    else:
        print("❌ test_mode attribute not properly set")
        return False
    
    # Check for test mode configuration
    if 'if self.test_mode:' in content and 'self.max_concurrent_chunks = 1' in content:
        print("✅ Test mode configures serial execution (1 concurrent chunk)")
    else:
        print("❌ Test mode serial execution not properly configured")
        return False
    
    # Check for test_single_window method
    if 'async def test_single_window(self)' in content:
        print("✅ test_single_window method found")
    else:
        print("❌ test_single_window method missing")
        return False
    
    # Check for specific test window (2025-11-06)
    if 'datetime(2025, 11, 6, 0, 0, 0, tzinfo=timezone.utc)' in content:
        print("✅ Test window correctly set to 2025-11-06T00:00:00")
    else:
        print("❌ Test window not correctly configured")
        return False
    
    # Check for iterate_trades test mode handling
    if 'if self.test_mode:' in content and 'trades = await self.test_single_window()' in content:
        print("✅ iterate_trades properly handles test mode")
    else:
        print("❌ iterate_trades test mode handling missing")
        return False
    
    return True

def test_service_modifications():
    """Test that ContextService handles test mode."""
    print("\nTesting ContextService modifications...")
    
    # Read the service.py file
    with open('backend/app/context/service.py', 'r') as f:
        content = f.read()
    
    # Check for test mode detection
    if 'if hasattr(provider, \'test_mode\') and provider.test_mode:' in content:
        print("✅ ContextService detects test mode")
    else:
        print("❌ ContextService test mode detection missing")
        return False
    
    # Check for test mode logging
    if '=== BACKFILL TEST MODE DETECTED ===' in content:
        print("✅ Test mode logging added")
    else:
        print("❌ Test mode logging missing")
        return False
    
    # Check for test_single_window call
    if 'trades = await provider.test_single_window()' in content:
        print("✅ ContextService calls test_single_window in test mode")
    else:
        print("❌ ContextService test_single_window call missing")
        return False
    
    return True

def test_http_client_enhancements():
    """Test that HTTP client has test mode logging."""
    print("\nTesting BinanceHttpClient enhancements...")
    
    # Read the backfill.py file
    with open('backend/app/context/backfill.py', 'r') as f:
        content = f.read()
    
    # Check for test mode logging in fetch
    if 'if self.settings.context_backfill_test_mode:' in content and 'logger.info(f"HTTP Request: GET {url}")' in content:
        print("✅ HTTP request logging for test mode added")
    else:
        print("❌ HTTP request logging for test mode missing")
        return False
    
    # Check for signature preview logging
    if 'sig_preview = signature[:20] + "..."' in content:
        print("✅ Signature preview logging added")
    else:
        print("❌ Signature preview logging missing")
        return False
    
    # Check for enhanced error logging in test mode
    if 'logger.error(f"HTTP {resp.status} error in test mode!")' in content:
        print("✅ Enhanced error logging for test mode added")
    else:
        print("❌ Enhanced error logging for test mode missing")
        return False
    
    return True

def test_env_example():
    """Test that .env.example has been updated."""
    print("\nTesting .env.example updates...")
    
    # Read the .env.example file
    with open('.env.example', 'r') as f:
        content = f.read()
    
    # Check for test mode variable
    if 'CONTEXT_BACKFILL_TEST_MODE=false' in content:
        print("✅ CONTEXT_BACKFILL_TEST_MODE added to .env.example")
    else:
        print("❌ CONTEXT_BACKFILL_TEST_MODE missing from .env.example")
        return False
    
    # Check for test mode instructions
    if 'CONTEXT_BACKFILL_TEST_MODE=true' in content and 'python test_hmac_auth.py' in content:
        print("✅ Test mode instructions added to .env.example")
    else:
        print("❌ Test mode instructions missing from .env.example")
        return False
    
    return True

def test_test_script():
    """Test that test script exists and has required functionality."""
    print("\nTesting test script...")
    
    # Check if test script exists
    if not os.path.exists('test_hmac_auth.py'):
        print("❌ test_hmac_auth.py script missing")
        return False
    
    # Read the test script
    with open('test_hmac_auth.py', 'r') as f:
        content = f.read()
    
    # Check for main functionality
    if 'async def test_hmac_authentication():' in content:
        print("✅ Main test function found")
    else:
        print("❌ Main test function missing")
        return False
    
    # Check for Settings creation
    if 'settings = get_settings()' in content:
        print("✅ Settings loading in test script")
    else:
        print("❌ Settings loading missing from test script")
        return False
    
    # Check for test mode enablement
    if 'settings.context_backfill_test_mode = True' in content:
        print("✅ Test mode enablement in test script")
    else:
        print("❌ Test mode enablement missing from test script")
        return False
    
    # Check for test_single_window call
    if 'trades = await history.test_single_window()' in content:
        print("✅ test_single_window call in test script")
    else:
        print("❌ test_single_window call missing from test script")
        return False
    
    return True

def test_requirements():
    """Test that requirements.txt has been updated."""
    print("\nTesting requirements.txt updates...")
    
    # Read the requirements.txt file
    with open('backend/requirements.txt', 'r') as f:
        content = f.read()
    
    # Check for pydantic
    if 'pydantic==' in content:
        print("✅ pydantic added to requirements.txt")
    else:
        print("❌ pydantic missing from requirements.txt")
        return False
    
    return True

def main():
    """Run all verification tests."""
    print("🔍 Verifying HMAC Test Mode Implementation")
    print("=" * 50)
    
    tests = [
        test_settings_structure,
        test_backfill_modifications,
        test_service_modifications,
        test_http_client_enhancements,
        test_env_example,
        test_test_script,
        test_requirements,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        else:
            print(f"❌ {test.__name__} failed")
    
    print("\n" + "=" * 50)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All verification tests passed!")
        print("\nImplementation Summary:")
        print("- ✅ Added CONTEXT_BACKFILL_TEST_MODE setting")
        print("- ✅ Implemented serial test execution (1 concurrent chunk)")
        print("- ✅ Added test_single_window method for 1-hour window test")
        print("- ✅ Enhanced logging for debugging HMAC authentication")
        print("- ✅ Modified ContextService to handle test mode")
        print("- ✅ Created test script and documentation")
        print("\nReady for testing:")
        print("1. Set BINANCE_API_KEY and BINANCE_API_SECRET")
        print("2. Set CONTEXT_BACKFILL_TEST_MODE=true")
        print("3. Run: python test_hmac_auth.py")
        return True
    else:
        print("❌ Some verification tests failed")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)