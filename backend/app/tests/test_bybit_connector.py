"""Unit tests for Bybit connector wrapper."""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.data_sources.bybit_connector import (
    BybitConnector,
    BybitConnectorRunner,
)
from app.ws.models import Settings, TradeSide, TradeTick


class TestBybitConnectorRunner:
    """Test the Bybit connector runner subprocess manager."""

    @pytest.mark.asyncio
    async def test_runner_initialization(self) -> None:
        """Test runner initializes properly."""
        settings = Settings()
        config = {"exchange": "bybit", "symbol": "BTCUSDT", "subscriptions": []}
        runner = BybitConnectorRunner(config, settings)

        assert runner.process is None
        assert runner._read_task is None
        assert runner._error_count == 0
        assert runner.event_queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_runner_config_building(self) -> None:
        """Test that runner builds configuration correctly."""
        settings = Settings()
        config = {
            "exchange": "bybit",
            "symbol": "BTCUSDT",
            "subscriptions": ["trades", "depth"],
            "api_key": "test_key",
            "api_secret": "test_secret",
        }
        runner = BybitConnectorRunner(config, settings)

        assert runner.connector_config["exchange"] == "bybit"
        assert runner.connector_config["symbol"] == "BTCUSDT"

    def test_runner_process_health_check(self) -> None:
        """Test checking if process is alive."""
        settings = Settings()
        config = {"exchange": "bybit", "symbol": "BTCUSDT"}
        runner = BybitConnectorRunner(config, settings)

        # Initially no process
        assert not runner.is_process_alive()

        # With mock process
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process is alive
        runner.process = mock_process

        assert runner.is_process_alive()

        # Dead process
        mock_process.poll.return_value = 1  # Process exited
        assert not runner.is_process_alive()

    def test_runner_health_status(self) -> None:
        """Test getting runner health status."""
        settings = Settings()
        config = {"exchange": "bybit", "symbol": "BTCUSDT"}
        runner = BybitConnectorRunner(config, settings)

        health = runner.get_health_status()
        assert health["process_alive"] is False
        assert health["pid"] is None
        assert health["queue_size"] == 0
        assert health["error_count"] == 0

    @pytest.mark.asyncio
    async def test_runner_send_command(self) -> None:
        """Test sending commands to subprocess."""
        settings = Settings()
        config = {"exchange": "bybit", "symbol": "BTCUSDT"}
        runner = BybitConnectorRunner(config, settings)

        # Mock process stdin
        mock_stdin = MagicMock()
        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        runner.process = mock_process

        await runner.send_command("subscribe", {"channel": "trades", "symbol": "BTCUSDT"})

        # Verify stdin write was called
        mock_stdin.write.assert_called_once()
        mock_stdin.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_runner_next_event_timeout(self) -> None:
        """Test getting event with timeout."""
        settings = Settings()
        config = {"exchange": "bybit", "symbol": "BTCUSDT"}
        runner = BybitConnectorRunner(config, settings)

        # Should timeout when queue is empty
        event = await runner.next_event()
        assert event is None

    @pytest.mark.asyncio
    async def test_runner_next_event_success(self) -> None:
        """Test getting event from queue."""
        settings = Settings()
        config = {"exchange": "bybit", "symbol": "BTCUSDT"}
        runner = BybitConnectorRunner(config, settings)

        # Put an event in the queue
        test_event = {
            "type": "trade",
            "timestamp": 1717440000000,
            "price": 100.5,
            "qty": 0.25,
            "side": "buy",
            "is_buyer_maker": False,
            "id": 1,
        }
        await runner.event_queue.put(test_event)

        event = await runner.next_event()
        assert event == test_event


class TestBybitConnector:
    """Test the Bybit connector wrapper."""

    def test_bybit_connector_initialization(self) -> None:
        """Test connector initializes properly."""
        settings = Settings()
        connector = BybitConnector(settings)

        assert not connector._connected
        assert not connector._subscribed_trades
        assert not connector._subscribed_depth
        assert connector._runner is None
        assert connector.settings == settings

    def test_bybit_connector_config_building(self) -> None:
        """Test connector builds configuration correctly."""
        settings = Settings()
        settings.bybit_api_key = "test_key"
        settings.bybit_api_secret = "test_secret"
        connector = BybitConnector(settings)

        config = connector._connector_config
        assert config["exchange"] == "bybit"
        assert config["symbol"] == settings.symbol
        assert config.get("api_key") == "test_key"
        assert config.get("api_secret") == "test_secret"

    def test_bybit_connector_config_without_api_keys(self) -> None:
        """Test connector config when API keys are not provided."""
        settings = Settings()
        settings.bybit_api_key = None
        settings.bybit_api_secret = None
        connector = BybitConnector(settings)

        config = connector._connector_config
        assert "api_key" not in config
        assert "api_secret" not in config

    @pytest.mark.asyncio
    async def test_bybit_connector_connect_failure(self) -> None:
        """Test connection failure handling."""
        settings = Settings()
        connector = BybitConnector(settings)

        with patch.object(BybitConnectorRunner, "start", side_effect=RuntimeError("Connection failed")):
            with pytest.raises(RuntimeError):
                await connector.connect()

        assert not connector._connected
        assert connector._runner is None

    @pytest.mark.asyncio
    async def test_bybit_connector_disconnect(self) -> None:
        """Test disconnection."""
        settings = Settings()
        connector = BybitConnector(settings)

        # Create a mock runner
        mock_runner = AsyncMock(spec=BybitConnectorRunner)
        connector._runner = mock_runner
        connector._connected = True

        await connector.disconnect()

        assert not connector._connected
        mock_runner.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_bybit_connector_subscribe_trades(self) -> None:
        """Test subscribing to trades."""
        settings = Settings()
        connector = BybitConnector(settings)

        # Create a mock runner
        mock_runner = AsyncMock(spec=BybitConnectorRunner)
        connector._runner = mock_runner

        await connector.subscribe_trades("BTCUSDT")

        assert connector._subscribed_trades
        mock_runner.send_command.assert_called_once_with(
            "subscribe", {"channel": "trades", "symbol": "BTCUSDT"}
        )

    @pytest.mark.asyncio
    async def test_bybit_connector_subscribe_depth(self) -> None:
        """Test subscribing to depth."""
        settings = Settings()
        connector = BybitConnector(settings)

        # Create a mock runner
        mock_runner = AsyncMock(spec=BybitConnectorRunner)
        connector._runner = mock_runner

        await connector.subscribe_depth("BTCUSDT")

        assert connector._subscribed_depth
        mock_runner.send_command.assert_called_once_with(
            "subscribe", {"channel": "depth", "symbol": "BTCUSDT"}
        )

    @pytest.mark.asyncio
    async def test_bybit_connector_next_event(self) -> None:
        """Test getting next event."""
        settings = Settings()
        connector = BybitConnector(settings)

        # Create a mock runner
        mock_runner = AsyncMock(spec=BybitConnectorRunner)
        test_event = {"type": "trade", "timestamp": 1717440000000}
        mock_runner.next_event.return_value = test_event
        connector._runner = mock_runner

        event = await connector.next_event()

        assert event == test_event
        assert connector._last_event_time is not None

    @pytest.mark.asyncio
    async def test_bybit_connector_is_connected(self) -> None:
        """Test connection status check."""
        settings = Settings()
        connector = BybitConnector(settings)

        # Not connected initially
        assert not await connector.is_connected()

        # Connected
        mock_runner = MagicMock()
        mock_runner.is_process_alive.return_value = True
        connector._connected = True
        connector._runner = mock_runner

        assert await connector.is_connected()

    def test_bybit_connector_health_status(self) -> None:
        """Test getting health status."""
        settings = Settings()
        connector = BybitConnector(settings)

        health = connector.get_health_status()
        assert health["connected"] is False
        assert health["subscribed_trades"] is False
        assert health["subscribed_depth"] is False
        assert health["last_event_time"] is None

    def test_bybit_connector_health_status_with_runner(self) -> None:
        """Test health status with active runner."""
        settings = Settings()
        connector = BybitConnector(settings)

        mock_runner = MagicMock()
        mock_runner.get_health_status.return_value = {"pid": 12345, "queue_size": 5}
        connector._runner = mock_runner
        connector._connected = True
        connector._subscribed_trades = True

        health = connector.get_health_status()
        assert health["connected"] is True
        assert health["subscribed_trades"] is True
        assert health["runner_health"]["pid"] == 12345


class TestBybitConnectorIntegration:
    """Integration tests for Bybit connector."""

    @pytest.mark.asyncio
    async def test_connector_lifecycle(self) -> None:
        """Test full connector lifecycle."""
        settings = Settings()
        connector = BybitConnector(settings)

        # Create mock runner
        mock_runner = AsyncMock(spec=BybitConnectorRunner)
        mock_runner.is_process_alive.return_value = True
        connector._runner = mock_runner
        connector._connected = True

        # Simulate subscribe operations
        await connector.subscribe_trades(settings.symbol)
        await connector.subscribe_depth(settings.symbol)

        assert connector._subscribed_trades
        assert connector._subscribed_depth
        assert mock_runner.send_command.call_count == 2

    @pytest.mark.asyncio
    async def test_connector_with_stubbed_runner(self) -> None:
        """Test connector using a stubbed runner for testing."""
        settings = Settings()
        connector = BybitConnector(settings)

        class StubbedRunner:
            """Stub runner for testing."""

            def __init__(self, config: dict, settings: Settings):
                self.config = config
                self.settings = settings
                self.alive = True
                self.event_queue = asyncio.Queue()

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                self.alive = False

            async def subscribe_trades(self, symbol: str) -> None:
                pass

            async def subscribe_depth(self, symbol: str) -> None:
                pass

            async def send_command(self, command: str, params: dict) -> None:
                pass

            async def next_event(self):
                try:
                    return await asyncio.wait_for(self.event_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    return None

            def is_process_alive(self) -> bool:
                return self.alive

            def get_health_status(self) -> dict:
                return {"process_alive": self.alive}

        # Use stubbed runner
        stubbed = StubbedRunner(connector._connector_config, settings)
        connector._runner = stubbed
        connector._connected = True

        # Add an event
        test_event = {
            "type": "trade",
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "price": 100.5,
            "qty": 0.25,
            "side": "buy",
            "is_buyer_maker": False,
            "id": 1,
        }
        await stubbed.event_queue.put(test_event)

        # Get the event
        event = await connector.next_event()
        assert event == test_event

    @pytest.mark.asyncio
    async def test_connector_process_failure_recovery(self) -> None:
        """Test that connector detects when process dies."""
        settings = Settings()
        connector = BybitConnector(settings)

        # Create mock runner that appears to die
        mock_runner = MagicMock(spec=BybitConnectorRunner)
        mock_runner.is_process_alive.return_value = False  # Process is dead
        connector._runner = mock_runner
        connector._connected = True

        # First check - process is dead
        result = await connector.is_connected()
        assert result is False

        # Verify process alive was called
        mock_runner.is_process_alive.assert_called()


class TestConnectorConfiguration:
    """Test connector configuration from settings."""

    def test_settings_bybit_connector_config_file(self) -> None:
        """Test Bybit connector config file setting."""
        import os

        os.environ["BYBIT_CONNECTOR_CONFIG_FILE"] = "/path/to/config.json"
        try:
            from app.ws.models import get_settings

            get_settings.cache_clear()
            settings = Settings()
            assert settings.bybit_connector_config_file == "/path/to/config.json"
        finally:
            if "BYBIT_CONNECTOR_CONFIG_FILE" in os.environ:
                del os.environ["BYBIT_CONNECTOR_CONFIG_FILE"]
            get_settings.cache_clear()

    def test_settings_bybit_connector_testnet(self) -> None:
        """Test Bybit connector testnet setting."""
        import os

        os.environ["BYBIT_CONNECTOR_TESTNET"] = "true"
        try:
            from app.ws.models import get_settings

            get_settings.cache_clear()
            settings = Settings()
            assert settings.bybit_connector_testnet is True
        finally:
            if "BYBIT_CONNECTOR_TESTNET" in os.environ:
                del os.environ["BYBIT_CONNECTOR_TESTNET"]
            get_settings.cache_clear()

    def test_settings_data_source_bybit_connector(self) -> None:
        """Test DATA_SOURCE=bybit_connector setting."""
        import os

        os.environ["DATA_SOURCE"] = "bybit_connector"
        try:
            from app.ws.models import get_settings

            get_settings.cache_clear()
            settings = Settings()
            assert settings.data_source == "bybit_connector"
        finally:
            if "DATA_SOURCE" in os.environ:
                del os.environ["DATA_SOURCE"]
            get_settings.cache_clear()


class TestBybitConnectorEventParsing:
    """Test event parsing through connector."""

    @pytest.mark.asyncio
    async def test_connector_receives_and_forwards_trade_event(self) -> None:
        """Test that connector properly receives and forwards trade events."""
        settings = Settings()
        connector = BybitConnector(settings)

        # Create mock runner with event
        mock_runner = AsyncMock(spec=BybitConnectorRunner)
        test_event = {
            "type": "trade",
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "price": 50000.0,
            "qty": 1.0,
            "side": "buy",
            "is_buyer_maker": False,
            "id": 999,
        }
        mock_runner.next_event.return_value = test_event
        mock_runner.is_process_alive.return_value = True
        connector._runner = mock_runner
        connector._connected = True

        # Get event
        event = await connector.next_event()

        assert event["type"] == "trade"
        assert event["price"] == 50000.0
        assert event["side"] == "buy"

    @pytest.mark.asyncio
    async def test_connector_receives_and_forwards_depth_event(self) -> None:
        """Test that connector properly receives and forwards depth events."""
        settings = Settings()
        connector = BybitConnector(settings)

        # Create mock runner with depth event
        mock_runner = AsyncMock(spec=BybitConnectorRunner)
        test_event = {
            "type": "depth",
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "bids": [[100.0, 1.0], [99.9, 2.0]],
            "asks": [[100.1, 1.5], [100.2, 2.5]],
            "last_update_id": 42,
        }
        mock_runner.next_event.return_value = test_event
        mock_runner.is_process_alive.return_value = True
        connector._runner = mock_runner
        connector._connected = True

        # Get event
        event = await connector.next_event()

        assert event["type"] == "depth"
        assert len(event["bids"]) == 2
        assert len(event["asks"]) == 2

    @pytest.mark.asyncio
    async def test_connector_handles_missing_runner(self) -> None:
        """Test that connector gracefully handles missing runner."""
        settings = Settings()
        connector = BybitConnector(settings)

        # No runner set
        event = await connector.next_event()
        assert event is None
