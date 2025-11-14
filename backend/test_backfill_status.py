#!/usr/bin/env python3
"""Test script to check backfill status."""
import asyncio
import logging
from app.context.service import ContextService
from app.ws.models import Settings

logging.basicConfig(level=logging.INFO, format="%(message)s")

async def main():
    settings = Settings()
    print(f"Settings:")
    print(f"  DATA_SOURCE: {settings.data_source}")
    print(f"  CONTEXT_BACKFILL_ENABLED: {settings.context_backfill_enabled}")
    print(f"  CONTEXT_BOOTSTRAP_PREV_DAY: {settings.context_bootstrap_prev_day}")
    print(f"  SYMBOL: {settings.symbol}")
    print()
    
    service = ContextService(settings=settings)
    await service.startup()
    
    # Wait a bit for backfill to start
    await asyncio.sleep(1)
    
    status = service.get_backfill_status()
    print(f"Backfill Status:")
    print(f"  status: {status['status']}")
    print(f"  running: {status['running']}")
    print(f"  complete: {status['complete']}")
    print(f"  progress: {status['progress']}")
    print()
    
    # Check prev day levels
    print(f"Previous Day Levels:")
    for key, value in service.prev_day_levels.items():
        print(f"  {key}: {value}")
    print()
    
    # Check OR
    print(f"Opening Range:")
    print(f"  or_start: {service.or_start}")
    print(f"  or_end: {service.or_end}")
    print(f"  or_high: {service.or_high}")
    print(f"  or_low: {service.or_low}")
    print()
    
    # Wait for backfill to complete (with timeout)
    print("Waiting up to 30s for backfill...")
    completed = await service.wait_for_backfill(timeout=30.0)
    print(f"Backfill completed: {completed}")
    
    status = service.get_backfill_status()
    print(f"\nFinal Backfill Status:")
    print(f"  status: {status['status']}")
    print(f"  complete: {status['complete']}")
    print()
    
    print(f"Previous Day Levels (after backfill):")
    for key, value in service.prev_day_levels.items():
        print(f"  {key}: {value}")
    print()
    
    await service.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
