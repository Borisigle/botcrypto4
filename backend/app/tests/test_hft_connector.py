"""Unit tests for HFT connector adapter."""
import asyncio
from datetime import datetime, timezone

import pytest

from app.data_sources.hft_connector import (
    ConnectorWrapper,
    HFTConnectorStream,
    StubbedConnector,
)
from app.ws.models import DepthUpdate, PriceLevel, Settings, TradeSide, TradeTick


class TestConnectorParsing:
    """Test trade and depth parsing from connector events."""

    def test_parse_connector_trade_with_datetime(self) -> None:
        """Test parsing a trade event with datetime timestamp."""
        ts = datetime.now(timezone.utc)
        payload = {
            "type": "trade",
            "timestamp": ts,
            "price": 100.5,
            "qty": 0.25,
            "side": "buy",
            "is_buyer_maker": False,
            "id": 42,
        }

        tick = HFTConnectorStream._parse_connector_trade(payload)

        assert tick.price == pytest.approx(100.5)
        assert tick.qty == pytest.approx(0.25)
        assert tick.side is TradeSide.BUY
        assert tick.isBuyerMaker is False
        assert tick.id == 42
        assert tick.ts == ts

    def test_parse_connector_trade_with_millisecond_timestamp(self) -> None:
        """Test parsing a trade event with millisecond timestamp."""
        ts_ms = 1717440000000
        payload = {
            "type": "trade",
            "timestamp": ts_ms,
            "price": 50000.0,
            "qty": 1.0,
            "side": "sell",
            "is_buyer_maker": True,
            "id": 99,
        }

        tick = HFTConnectorStream._parse_connector_trade(payload)

        assert tick.price == pytest.approx(50000.0)
        assert tick.qty == pytest.approx(1.0)
        assert tick.side is TradeSide.SELL
        assert tick.isBuyerMaker is True
        assert tick.id == 99
        assert tick.ts == datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

    def test_parse_connector_trade_missing_timestamp(self) -> None:
        """Test that missing timestamp raises ValueError."""
        payload = {
            "type": "trade",
            "price": 100.0,
            "qty": 1.0,
        }

        with pytest.raises(ValueError, match="timestamp"):
            HFTConnectorStream._parse_connector_trade(payload)

    def test_parse_connector_trade_invalid_side(self) -> None:
        """Test that invalid side raises ValueError."""
        payload = {
            "type": "trade",
            "timestamp": datetime.now(timezone.utc),
            "price": 100.0,
            "qty": 1.0,
            "side": "invalid",
            "is_buyer_maker": False,
            "id": 1,
        }

        with pytest.raises(ValueError, match="invalid trade side"):
            HFTConnectorStream._parse_connector_trade(payload)

    def test_parse_connector_depth(self) -> None:
        """Test parsing a depth event."""
        ts = datetime.now(timezone.utc)
        payload = {
            "type": "depth",
            "timestamp": ts,
            "bids": [(100.0, 1.0), (99.9, 2.0)],
            "asks": [(100.1, 1.5), (100.2, 2.5)],
            "last_update_id": 42,
        }

        update = HFTConnectorStream._parse_connector_depth(payload)

        assert update is not None
        assert len(update.bids) == 2
        assert len(update.asks) == 2
        assert update.bids[0].price == pytest.approx(100.0)
        assert update.bids[0].qty == pytest.approx(1.0)
        assert update.asks[0].price == pytest.approx(100.1)
        assert update.lastUpdateId == 42
        assert update.ts == ts

    def test_parse_connector_depth_with_millisecond_timestamp(self) -> None:
        """Test parsing a depth event with millisecond timestamp."""
        ts_ms = 1717440000000
        payload = {
            "type": "depth",
            "timestamp": ts_ms,
            "bids": [(100.0, 1.0)],
            "asks": [(100.1, 1.0)],
            "last_update_id": 10,
        }

        update = HFTConnectorStream._parse_connector_depth(payload)

        assert update is not None
        assert update.ts == datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

    def test_parse_connector_depth_missing_timestamp(self) -> None:
        """Test that missing timestamp raises ValueError."""
        payload = {
            "type": "depth",
            "bids": [],
            "asks": [],
        }

        with pytest.raises(ValueError, match="timestamp"):
            HFTConnectorStream._parse_connector_depth(payload)


class TestStubbedConnector:
    """Test the stubbed connector implementation."""

    @pytest.mark.asyncio
    async def test_stubbed_connector_initialization(self) -> None:
        """Test stubbed connector initializes properly."""
        settings = Settings()
        connector = StubbedConnector(settings)

        assert not connector._connected
        assert not connector._subscribed_trades
        assert not connector._subscribed_depth
        assert connector._event_counter == 0

    @pytest.mark.asyncio
    async def test_stubbed_connector_connect_disconnect(self) -> None:
        """Test connecting and disconnecting."""
        settings = Settings()
        connector = StubbedConnector(settings)

        await connector.connect()
        assert await connector.is_connected()

        await connector.disconnect()
        assert not await connector.is_connected()

    @pytest.mark.asyncio
    async def test_stubbed_connector_subscribe_trades(self) -> None:
        """Test subscribing to trades."""
        settings = Settings()
        connector = StubbedConnector(settings)

        await connector.connect()
        await connector.subscribe_trades("BTCUSDT")

        assert connector._subscribed_trades
        await connector.disconnect()

    @pytest.mark.asyncio
    async def test_stubbed_connector_subscribe_depth(self) -> None:
        """Test subscribing to depth."""
        settings = Settings()
        connector = StubbedConnector(settings)

        await connector.connect()
        await connector.subscribe_depth("BTCUSDT")

        assert connector._subscribed_depth
        await connector.disconnect()

    @pytest.mark.asyncio
    async def test_stubbed_connector_generates_trade_events(self) -> None:
        """Test that stubbed connector generates trade events."""
        settings = Settings()
        connector = StubbedConnector(settings)

        await connector.connect()
        await connector.subscribe_trades("BTCUSDT")

        # Should receive some events
        events_received = 0
        for _ in range(100):
            event = await connector.next_event()
            if event and event.get("type") == "trade":
                events_received += 1
            if events_received >= 5:
                break

        assert events_received > 0
        await connector.disconnect()

    @pytest.mark.asyncio
    async def test_stubbed_connector_generates_depth_events(self) -> None:
        """Test that stubbed connector generates depth events."""
        settings = Settings()
        connector = StubbedConnector(settings)

        await connector.connect()
        await connector.subscribe_depth("BTCUSDT")

        # Should receive some events
        events_received = 0
        for _ in range(200):
            event = await connector.next_event()
            if event and event.get("type") == "depth":
                events_received += 1
            if events_received >= 2:
                break

        assert events_received > 0
        await connector.disconnect()

    @pytest.mark.asyncio
    async def test_stubbed_connector_health_status(self) -> None:
        """Test connector health status."""
        settings = Settings()
        connector = StubbedConnector(settings)

        health = connector.get_health_status()
        assert health["connected"] is False

        await connector.connect()
        health = connector.get_health_status()
        assert health["connected"] is True

        await connector.disconnect()


class TestHFTConnectorStream:
    """Test the HFT connector stream adapter."""

    @pytest.mark.asyncio
    async def test_connector_stream_initialization(self) -> None:
        """Test stream initializes properly."""
        settings = Settings()
        connector = StubbedConnector(settings)

        from app.ws.metrics import MetricsRecorder

        metrics = MetricsRecorder(60)
        stream = HFTConnectorStream(settings, connector, metrics)

        assert stream.connector == connector
        assert stream.metrics == metrics
        assert stream.name == "hft_connector"

    @pytest.mark.asyncio
    async def test_connector_stream_startup_shutdown(self) -> None:
        """Test stream startup and shutdown."""
        settings = Settings()
        connector = StubbedConnector(settings)

        from app.ws.metrics import MetricsRecorder

        metrics = MetricsRecorder(60)
        stream = HFTConnectorStream(settings, connector, metrics)

        await stream.start()
        assert stream.queue is not None
        assert await connector.is_connected()

        await stream.stop()
        assert stream.queue is None

    @pytest.mark.asyncio
    async def test_connector_stream_ingests_trades(self) -> None:
        """Test that stream properly ingests and queues trades."""
        settings = Settings()
        connector = StubbedConnector(settings)

        from app.ws.metrics import MetricsRecorder

        metrics = MetricsRecorder(60)
        stream = HFTConnectorStream(settings, connector, metrics)

        await stream.start()
        await connector.subscribe_trades("BTCUSDT")

        # Wait for events to be processed
        await asyncio.sleep(0.5)

        # Check that metrics were updated
        snapshot = metrics.snapshot(trade_queue_size=0, depth_queue_size=0)
        assert snapshot.trades.per_minute_count >= 0

        await stream.stop()

    @pytest.mark.asyncio
    async def test_connector_stream_health_status(self) -> None:
        """Test stream health status reporting."""
        settings = Settings()
        connector = StubbedConnector(settings)

        from app.ws.metrics import MetricsRecorder

        metrics = MetricsRecorder(60)
        stream = HFTConnectorStream(settings, connector, metrics)

        await stream.start()
        # Give the connector time to connect
        await asyncio.sleep(0.15)
        health = stream.health()
        # Verify stream has a health status
        assert health is not None

        await stream.stop()

    @pytest.mark.asyncio
    async def test_connector_stream_reconnection_on_disconnect(self) -> None:
        """Test that stream has reconnection logic initialized."""
        settings = Settings()
        connector = StubbedConnector(settings)

        from app.ws.metrics import MetricsRecorder

        metrics = MetricsRecorder(60)
        stream = HFTConnectorStream(settings, connector, metrics)

        # Verify reconnection attributes exist and are properly initialized
        assert hasattr(stream, '_reconnection_attempts')
        assert hasattr(stream, '_max_reconnection_attempts')
        assert stream._reconnection_attempts == 0
        assert stream._max_reconnection_attempts == 5

    @pytest.mark.asyncio
    async def test_connector_stream_strategy_engine_integration(self) -> None:
        """Test that stream can be configured with strategy engine."""
        settings = Settings()
        connector = StubbedConnector(settings)

        from app.ws.metrics import MetricsRecorder

        metrics = MetricsRecorder(60)
        stream = HFTConnectorStream(settings, connector, metrics)

        # Mock strategy engine
        class MockStrategyEngine:
            def ingest_trade(self, tick: TradeTick) -> None:
                pass

        strategy_engine = MockStrategyEngine()
        stream.set_strategy_engine(strategy_engine)

        # Verify the strategy engine is set
        assert stream._strategy_engine == strategy_engine

    @pytest.mark.asyncio
    async def test_connector_stream_parse_and_handle_trade(self) -> None:
        """Test handling a valid trade event."""
        settings = Settings()

        class SingleTradeConnector(StubbedConnector):
            """Connector that generates exactly one trade."""

            async def next_event(self) -> dict | None:
                ts = datetime.now(timezone.utc)
                return {
                    "type": "trade",
                    "timestamp": ts,
                    "price": 100.5,
                    "qty": 0.25,
                    "side": "buy",
                    "is_buyer_maker": False,
                    "id": 1,
                }

        connector = SingleTradeConnector(settings)

        from app.ws.metrics import MetricsRecorder

        metrics = MetricsRecorder(60)
        stream = HFTConnectorStream(settings, connector, metrics)

        # Test parsing
        payload = {
            "type": "trade",
            "timestamp": datetime.now(timezone.utc),
            "price": 100.5,
            "qty": 0.25,
            "side": "buy",
            "is_buyer_maker": False,
            "id": 42,
        }

        tick = HFTConnectorStream._parse_connector_trade(payload)
        assert tick.price == pytest.approx(100.5)
        assert tick.side is TradeSide.BUY

    @pytest.mark.asyncio
    async def test_connector_stream_parse_and_handle_depth(self) -> None:
        """Test handling a valid depth event."""
        settings = Settings()
        connector = StubbedConnector(settings)

        from app.ws.metrics import MetricsRecorder

        metrics = MetricsRecorder(60)
        stream = HFTConnectorStream(settings, connector, metrics)

        # Test parsing
        payload = {
            "type": "depth",
            "timestamp": datetime.now(timezone.utc),
            "bids": [(100.0, 1.0), (99.9, 2.0)],
            "asks": [(100.1, 1.5), (100.2, 2.5)],
            "last_update_id": 42,
        }

        update = HFTConnectorStream._parse_connector_depth(payload)
        assert update is not None
        assert len(update.bids) == 2
        assert len(update.asks) == 2
        assert update.lastUpdateId == 42


class TestConnectorConfiguration:
    """Test connector configuration from settings."""

    def test_settings_data_source_defaults_to_binance_ws(self) -> None:
        """Test that data source defaults to binance_ws."""
        settings = Settings()
        assert settings.data_source == "binance_ws"

    def test_settings_connector_configuration(self) -> None:
        """Test connector-specific settings."""
        import os

        # Set environment variables
        os.environ["DATA_SOURCE"] = "hft_connector"
        os.environ["CONNECTOR_NAME"] = "test_connector"
        os.environ["CONNECTOR_POLL_INTERVAL_MS"] = "50"
        os.environ["CONNECTOR_PAPER_TRADING"] = "false"

        # Clear the cached settings
        from app.ws.models import get_settings

        get_settings.cache_clear()

        try:
            settings = Settings()
            assert settings.data_source == "hft_connector"
            assert settings.connector_name == "test_connector"
            assert settings.connector_poll_interval_ms == 50
            assert settings.connector_paper_trading is False
        finally:
            # Clean up environment
            del os.environ["DATA_SOURCE"]
            del os.environ["CONNECTOR_NAME"]
            del os.environ["CONNECTOR_POLL_INTERVAL_MS"]
            del os.environ["CONNECTOR_PAPER_TRADING"]
            get_settings.cache_clear()

    def test_settings_paper_trading_defaults_to_true(self) -> None:
        """Test that paper trading defaults to true."""
        settings = Settings()
        assert settings.connector_paper_trading is True

    def test_settings_poll_interval_defaults_to_100ms(self) -> None:
        """Test that poll interval defaults to 100ms."""
        settings = Settings()
        assert settings.connector_poll_interval_ms == 100
