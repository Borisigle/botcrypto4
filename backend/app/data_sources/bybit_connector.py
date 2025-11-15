"""Bybit connector wrapper using hftbacktest.live.LiveClient for live data streaming."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from app.data_sources.hft_connector import ConnectorWrapper
from app.ws.client import structured_log
from app.ws.models import Settings

if TYPE_CHECKING:
    pass


class BybitConnectorRunner:
    """Manages the connector subprocess and communication channel."""

    def __init__(
        self,
        connector_config: dict[str, Any],
        settings: Settings,
    ) -> None:
        """Initialize the Bybit connector runner.

        Args:
            connector_config: Configuration dict for the connector (symbols, channels, etc.)
            settings: Application settings
        """
        self.settings = settings
        self.connector_config = connector_config
        self.logger = logging.getLogger("data_sources.bybit_connector_runner")
        self.process: Optional[subprocess.Popen] = None
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self._read_task: Optional[asyncio.Task] = None
        self._health_check_count = 0
        self._error_count = 0

    async def start(self) -> None:
        """Start the connector subprocess."""
        if self.process is not None:
            return

        try:
            # Create a Python script to run the connector
            connector_script = self._create_connector_script()

            # Start the subprocess with Python
            self.process = subprocess.Popen(
                ["python", "-c", connector_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )

            # Start reading from subprocess
            self._read_task = asyncio.create_task(self._read_from_subprocess())

            structured_log(
                self.logger,
                "bybit_connector_runner_started",
                pid=self.process.pid if self.process else None,
            )
        except Exception as exc:
            structured_log(
                self.logger,
                "bybit_connector_runner_start_error",
                error=str(exc),
            )
            raise

    async def stop(self) -> None:
        """Stop the connector subprocess."""
        if self.process is None:
            return

        try:
            # Cancel the read task
            if self._read_task:
                self._read_task.cancel()
                try:
                    await self._read_task
                except asyncio.CancelledError:
                    pass
                self._read_task = None

            # Send SIGTERM to process
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()

            self.process = None
            structured_log(self.logger, "bybit_connector_runner_stopped")
        except Exception as exc:
            structured_log(
                self.logger,
                "bybit_connector_runner_stop_error",
                error=str(exc),
            )

    async def send_command(self, command: str, params: Optional[dict[str, Any]] = None) -> None:
        """Send a command to the connector subprocess.

        Args:
            command: Command name
            params: Optional parameters dict
        """
        if self.process is None or self.process.stdin is None:
            return

        try:
            msg = {"command": command}
            if params:
                msg.update(params)
            msg_str = json.dumps(msg) + "\n"
            self.process.stdin.write(msg_str)
            self.process.stdin.flush()
        except Exception as exc:
            structured_log(
                self.logger,
                "bybit_connector_send_error",
                command=command,
                error=str(exc),
            )

    async def next_event(self) -> Optional[dict[str, Any]]:
        """Get the next event from the queue with timeout."""
        try:
            event = await asyncio.wait_for(self.event_queue.get(), timeout=1.0)
            return event
        except asyncio.TimeoutError:
            return None

    def is_process_alive(self) -> bool:
        """Check if the subprocess is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def _create_connector_script(self) -> str:
        """Create the connector script that runs in the subprocess."""
        # Use inline Python to avoid subprocess management complexity
        script = f'''
import json
import sys
import asyncio
from datetime import datetime, timezone
from hftbacktest.live import create, run_live

# Configuration from parent process
config = {json.dumps(self.connector_config)}

async def main():
    try:
        # Initialize Live connector
        connector = await create("bybit", config, paper_trading={self.settings.connector_paper_trading})
        
        # Subscribe to channels
        await connector.subscribe(config.get('subscriptions', []))
        
        print(json.dumps({{'status': 'connected', 'connector': 'bybit'}}) + "\\n", flush=True)
        
        # Event loop
        while True:
            try:
                # Get next event with timeout
                event = await asyncio.wait_for(connector.next_event(), timeout=1.0)
                if event:
                    # Parse and format the event
                    formatted_event = format_event(event)
                    print(json.dumps(formatted_event) + "\\n", flush=True)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(json.dumps({{'status': 'error', 'error': str(e)}}) + "\\n", flush=True)
                break
    except Exception as e:
        print(json.dumps({{'status': 'error', 'error': str(e)}}) + "\\n", flush=True)
        sys.exit(1)

def format_event(event):
    # Transform raw event to our format
    if hasattr(event, 'type'):
        if event.type == 'trade':
            return {{
                'type': 'trade',
                'timestamp': int(event.timestamp * 1000) if hasattr(event, 'timestamp') else None,
                'price': float(event.price),
                'qty': float(event.qty),
                'side': 'buy' if event.side == 1 else 'sell',
                'is_buyer_maker': bool(event.is_buyer_maker) if hasattr(event, 'is_buyer_maker') else False,
                'id': int(event.id),
            }}
        elif event.type == 'depth':
            return {{
                'type': 'depth',
                'timestamp': int(event.timestamp * 1000) if hasattr(event, 'timestamp') else None,
                'bids': [[float(p), float(q)] for p, q in (event.bids or [])],
                'asks': [[float(p), float(q)] for p, q in (event.asks or [])],
                'last_update_id': int(event.last_update_id) if hasattr(event, 'last_update_id') else 0,
            }}
    return event

if __name__ == '__main__':
    asyncio.run(main())
'''
        return script

    async def _read_from_subprocess(self) -> None:
        """Read events from the subprocess stdout."""
        if self.process is None or self.process.stdout is None:
            return

        try:
            loop = asyncio.get_event_loop()
            while self.process and self.process.poll() is None:
                try:
                    # Read a line from subprocess
                    line = await loop.run_in_executor(None, self.process.stdout.readline)
                    if not line:
                        await asyncio.sleep(0.01)
                        continue

                    # Parse JSON event
                    try:
                        event = json.loads(line.strip())
                        if event.get("status") == "connected":
                            structured_log(self.logger, "bybit_connector_subprocess_connected")
                        elif event.get("status") == "error":
                            self._error_count += 1
                            structured_log(
                                self.logger,
                                "bybit_connector_subprocess_error",
                                error=event.get("error"),
                                error_count=self._error_count,
                            )
                        else:
                            # Regular event (trade/depth)
                            await self.event_queue.put(event)
                    except json.JSONDecodeError:
                        pass
                except Exception as exc:
                    structured_log(
                        self.logger,
                        "bybit_connector_read_error",
                        error=str(exc),
                    )
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            structured_log(
                self.logger,
                "bybit_connector_read_fatal_error",
                error=str(exc),
            )

    def get_health_status(self) -> dict[str, Any]:
        """Get runner health status."""
        return {
            "process_alive": self.is_process_alive(),
            "pid": self.process.pid if self.process else None,
            "queue_size": self.event_queue.qsize(),
            "error_count": self._error_count,
        }


class BybitConnector(ConnectorWrapper):
    """Bybit connector wrapper using hftbacktest LiveClient.

    This connector connects to Bybit's live API and streams trades and depth updates.
    It manages the connector subprocess lifecycle and translates events to standard formats.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the Bybit connector.

        Args:
            settings: Application settings
        """
        self.settings = settings
        self.logger = logging.getLogger("data_sources.bybit_connector")
        self._connected = False
        self._subscribed_trades = False
        self._subscribed_depth = False
        self._runner: Optional[BybitConnectorRunner] = None
        self._check_connection_task: Optional[asyncio.Task] = None
        self._last_event_time: Optional[datetime] = None

        # Build connector configuration
        self._connector_config = self._build_config()

    def _build_config(self) -> dict[str, Any]:
        """Build the connector configuration from settings."""
        # Build subscriptions list with trade and depth channels
        subscriptions = [
            {"channel": "trades", "symbol": self.settings.symbol},
            {"channel": "depth", "symbol": self.settings.symbol},
        ]
        
        config = {
            "exchange": "bybit",
            "symbol": self.settings.symbol,
            "subscriptions": subscriptions,
        }

        # Add API credentials if available
        if self.settings.bybit_api_key and self.settings.bybit_api_secret:
            config["api_key"] = self.settings.bybit_api_key
            config["api_secret"] = self.settings.bybit_api_secret

        return config

    async def connect(self) -> None:
        """Connect to the Bybit connector."""
        if self._connected:
            return

        try:
            self._runner = BybitConnectorRunner(self._connector_config, self.settings)
            await self._runner.start()

            # Wait a bit for the subprocess to initialize
            await asyncio.sleep(0.5)

            if not self._runner.is_process_alive():
                raise RuntimeError("Connector process failed to start")

            self._connected = True
            self._check_connection_task = asyncio.create_task(self._check_connection_loop())

            structured_log(
                self.logger,
                "bybit_connector_connected",
                symbol=self.settings.symbol,
            )
        except Exception as exc:
            self._connected = False
            if self._runner:
                await self._runner.stop()
                self._runner = None
            structured_log(
                self.logger,
                "bybit_connector_connection_error",
                error=str(exc),
            )
            raise

    async def disconnect(self) -> None:
        """Disconnect from the Bybit connector."""
        if not self._connected:
            return

        try:
            self._connected = False

            # Cancel connection check task
            if self._check_connection_task:
                self._check_connection_task.cancel()
                try:
                    await self._check_connection_task
                except asyncio.CancelledError:
                    pass
                self._check_connection_task = None

            # Stop the runner
            if self._runner:
                await self._runner.stop()
                self._runner = None

            structured_log(self.logger, "bybit_connector_disconnected")
        except Exception as exc:
            structured_log(
                self.logger,
                "bybit_connector_disconnect_error",
                error=str(exc),
            )

    async def subscribe_trades(self, symbol: str) -> None:
        """Subscribe to trade updates for a symbol."""
        if self._subscribed_trades:
            return

        try:
            if self._runner:
                await self._runner.send_command("subscribe", {"channel": "trades", "symbol": symbol})
            self._subscribed_trades = True
            structured_log(
                self.logger,
                "bybit_connector_subscribed_trades",
                symbol=symbol,
            )
        except Exception as exc:
            structured_log(
                self.logger,
                "bybit_connector_subscribe_trades_error",
                error=str(exc),
            )

    async def subscribe_depth(self, symbol: str) -> None:
        """Subscribe to depth/L2 book updates for a symbol."""
        if self._subscribed_depth:
            return

        try:
            if self._runner:
                await self._runner.send_command("subscribe", {"channel": "depth", "symbol": symbol})
            self._subscribed_depth = True
            structured_log(
                self.logger,
                "bybit_connector_subscribed_depth",
                symbol=symbol,
            )
        except Exception as exc:
            structured_log(
                self.logger,
                "bybit_connector_subscribe_depth_error",
                error=str(exc),
            )

    async def next_event(self) -> Optional[dict[str, Any]]:
        """Get the next event from the connector.

        Returns None if no event is available within the timeout.
        Returns a dict with 'type' key: 'trade' or 'depth'.
        """
        if not self._runner:
            return None

        event = await self._runner.next_event()
        if event:
            self._last_event_time = datetime.now(timezone.utc)
        return event

    async def is_connected(self) -> bool:
        """Check if the connector is currently connected."""
        if not self._connected or not self._runner:
            return False
        return self._runner.is_process_alive()

    def get_health_status(self) -> dict[str, Any]:
        """Get connector health status."""
        runner_health = self._runner.get_health_status() if self._runner else {}
        return {
            "connected": self._connected,
            "subscribed_trades": self._subscribed_trades,
            "subscribed_depth": self._subscribed_depth,
            "last_event_time": self._last_event_time.isoformat() if self._last_event_time else None,
            "runner_health": runner_health,
        }

    async def _check_connection_loop(self) -> None:
        """Background task to monitor connection health."""
        try:
            while self._connected:
                if not self._runner or not self._runner.is_process_alive():
                    structured_log(
                        self.logger,
                        "bybit_connector_process_died",
                    )
                    self._connected = False
                    break
                await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            structured_log(
                self.logger,
                "bybit_connector_health_check_error",
                error=str(exc),
            )
