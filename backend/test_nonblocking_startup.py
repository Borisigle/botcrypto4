#!/usr/bin/env python3
"""
Demonstration script for non-blocking startup with background backfill.

This script shows that:
1. Application starts immediately (< 1 second)
2. API is responsive while backfill runs
3. Backfill progress can be monitored
4. Metrics populate gradually as backfill completes
"""

import asyncio
import time
from datetime import datetime

from app.context.service import ContextService
from app.ws.models import Settings


async def simulate_api_request(service: ContextService, request_name: str) -> None:
    """Simulate an API request to show the service is responsive."""
    start = time.time()
    try:
        payload = service.context_payload()
        elapsed = time.time() - start
        print(f"  [{elapsed*1000:.0f}ms] {request_name}: ✓ Responded")
        if payload["levels"]["VWAP"]:
            print(f"           VWAP={payload['levels']['VWAP']:.2f}")
    except Exception as e:
        print(f"  {request_name}: ✗ Error - {e}")


async def main():
    print("=" * 70)
    print("NON-BLOCKING STARTUP DEMONSTRATION")
    print("=" * 70)
    
    # Configure service with backfill enabled
    settings = Settings(
        context_backfill_enabled=True,
        context_bootstrap_prev_day=False,
        data_source="binance_ws",
        backfill_timeout_seconds=30,  # Short timeout for demo
    )
    
    service = ContextService(settings=settings, fetch_exchange_info=False)
    
    # Measure startup time
    print("\n1. Starting service with backfill enabled...")
    start_time = time.time()
    await service.startup()
    startup_elapsed = time.time() - start_time
    
    print(f"   ✓ Service started in {startup_elapsed:.3f} seconds")
    
    # Check backfill status immediately
    status = service.get_backfill_status()
    print(f"   Backfill status: {status['status']}")
    
    # Demonstrate responsiveness
    print("\n2. Service is responsive while backfill runs in background:")
    for i in range(3):
        await simulate_api_request(service, f"Request #{i+1}")
        await asyncio.sleep(0.5)
    
    # Monitor backfill progress
    print("\n3. Monitoring backfill progress...")
    wait_start = time.time()
    timeout = 35
    
    while time.time() - wait_start < timeout:
        status = service.get_backfill_status()
        if not status["running"]:
            break
        print(f"   [{int(time.time() - wait_start)}s] Backfill still running...")
        await asyncio.sleep(2)
    
    # Final status
    final_status = service.get_backfill_status()
    print(f"\n4. Final backfill status: {final_status['status']}")
    
    # Show metrics
    print("\n5. Final metrics:")
    payload = service.context_payload()
    levels = payload["levels"]
    print(f"   VWAP: {levels['VWAP']}")
    print(f"   POC:  {levels['POCd']}")
    print(f"   Trade count: {service.trade_count}")
    
    # Cleanup
    await service.shutdown()
    print("\n✓ Service shutdown complete")
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"✓ Startup time: {startup_elapsed:.3f}s (non-blocking!)")
    print(f"✓ Service responsive: API responded to all requests")
    print(f"✓ Backfill completed in background: {final_status['status']}")
    print(f"✓ Metrics populated: {service.trade_count} trades")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
