"""HFT Connector adapter for hftbacktest live wrapper integration."""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from app.ws.client import BaseStreamService, structured_log
from app.ws.models import DepthUpdate, PriceLevel, Settings, TradeSide, TradeTick

if TYPE_CHECKING:
    from app.context.service import ContextService


class ConnectorWrapper(ABC):
    """Abstract base class for exchange connector implementations."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the connector."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the connector."""

    @abstractmethod
    async def subscribe_trades(self, symbol: str) -> None:
        """Subscribe to trade updates for a symbol."""

    @abstractmethod
    async def subscribe_depth(self, symbol: str) -> None:
        """Subscribe to depth/L2 book updates for a symbol."""

    @abstractmethod
    async def next_event(self) -> Optional[dict[str, Any]]:
        """Get the next event from the connector.
        
        Returns None if no event is available within the timeout.
        Returns a dict with 'type' key: 'trade' or 'depth'.
        """

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check if the connector is currently connected."""

    @abstractmethod
    def get_health_status(self) -> dict[str, Any]:
        """Get connector health status."""


class HFTConnectorStream(BaseStreamService):
    """Background service for ingesting data from HFT connectors.
    
    This adapter wraps an exchange connector (e.g., hftbacktest) and converts
    its data into the standard TradeTick and DepthUpdate models.
    """

    def __init__(
        self,
        settings: Settings,
        connector: ConnectorWrapper,
        metrics: Any,
        context_service: Optional["ContextService"] = None,
    ) -> None:
        """Initialize the HFT connector stream.
        
        Args:
            settings: Application settings
            connector: The connector instance to use
            metrics: MetricsRecorder for tracking ingestion metrics
            context_service: Optional context service for trade ingestion
        """
        # Use a placeholder URL since we're not using websockets
        super().__init__("hft_connector", "connector://hft", settings)
        self.connector = connector
        self.metrics = metrics
        self.context_service = context_service
        self._strategy_engine = None
        self._reconnection_attempts = 0
        self._max_reconnection_attempts = 5
        self._reconnection_backoff = 1.0

    def set_strategy_engine(self, strategy_engine) -> None:
        """Set the strategy engine reference for trade forwarding."""
        self._strategy_engine = strategy_engine

    async def on_start(self) -> None:
        """Connect to the HFT connector."""
        await self._attempt_connect(initial=True)

    async def on_stop(self) -> None:
        """Disconnect from the HFT connector."""
        try:
            await self.connector.disconnect()
        except Exception as exc:
            structured_log(
                self.logger,
                "connector_disconnect_error",
                error=str(exc),
                connector="hft",
            )

    async def _connect_with_retry(self) -> bool:
        """Connect to the connector with exponential backoff."""
        attempts = 0
        backoff = 0.5
        raw_max_attempts = self._max_reconnection_attempts
        max_attempts = raw_max_attempts if raw_max_attempts is not None else 0
        unlimited = max_attempts <= 0
        last_error: Optional[Exception] = None

        self._reconnection_attempts = 0

        while (
            not self._stop_event.is_set()
            and (unlimited or attempts < max_attempts)
        ):
            try:
                await self.connector.connect()
                await self.connector.subscribe_trades(self.settings.symbol)
                await self.connector.subscribe_depth(self.settings.symbol)
                self._reconnection_attempts = 0
                self._reconnection_backoff = 1.0
                structured_log(
                    self.logger,
                    "connector_connected",
                    symbol=self.settings.symbol,
                    connector="hft",
                )
                self.logger.info("Connector stream started, receiving live data")
                return True
            except Exception as exc:
                attempts += 1
                last_error = exc
                self._reconnection_attempts = attempts
                structured_log(
                    self.logger,
                    "connector_connection_error",
                    attempt=attempts,
                    max_attempts=None if unlimited else max_attempts,
                    error=str(exc),
                    connector="hft",
                    retry_delay=round(backoff, 2),
                )
                if not unlimited and attempts >= max_attempts:
                    break
                if self._stop_event.is_set():
                    break
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    return False
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 10.0)

        if last_error:
            structured_log(
                self.logger,
                "connector_connection_exhausted",
                attempts=attempts,
                max_attempts=None if unlimited else max_attempts,
                error=str(last_error),
                connector="hft",
            )
        if attempts > 0:
            self._reconnection_backoff = min(
                max(self._reconnection_backoff, 1.0) * 2,
                60.0,
            )
        return False

    async def _attempt_connect(self, *, initial: bool = False) -> bool:
        success = await self._connect_with_retry()
        if success:
            self._handle_connected()
            return True

        if self._stop_event.is_set():
            return False

        raw_max_attempts = self._max_reconnection_attempts
        max_attempts_field = (
            None
            if raw_max_attempts is None or raw_max_attempts <= 0
            else raw_max_attempts
        )

        event_name = "connector_initial_connect_failed" if initial else "connector_reconnect_failed"
        structured_log(
            self.logger,
            event_name,
            connector="hft",
            attempts=self._reconnection_attempts,
            max_attempts=max_attempts_field,
            cooldown=round(max(self._reconnection_backoff, 1.0), 2),
        )
        return False

    async def _ensure_connected(self) -> bool:
        if self._stop_event.is_set():
            return False

        try:
            connected = await self.connector.is_connected()
        except Exception as exc:
            structured_log(
                self.logger,
                "connector_status_error",
                error=str(exc),
                connector="hft",
            )
            connected = False

        if connected:
            if not self.state.connected:
                self._handle_connected()
            return True

        if self.state.connected:
            structured_log(
                self.logger,
                "connector_disconnected",
                connector="hft",
            )
            self._handle_disconnected()

        return await self._attempt_connect()

    async def _cooldown_after_failure(self) -> None:
        if self._stop_event.is_set():
            return
        cooldown = min(max(self._reconnection_backoff, 1.0), 60.0)
        structured_log(
            self.logger,
            "connector_reconnect_cooldown",
            connector="hft",
            cooldown=round(cooldown, 2),
        )
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=cooldown)
        except asyncio.TimeoutError:
            pass

    async def _network_loop(self) -> None:
        """Event loop for receiving data from the connector."""
        while not self._stop_event.is_set():
            if not await self._ensure_connected():
                await self._cooldown_after_failure()
                continue

            try:
                event = await asyncio.wait_for(
                    self.connector.next_event(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                structured_log(
                    self.logger,
                    "connector_error",
                    error=str(exc),
                    connector="hft",
                )
                if self.state.connected:
                    self._handle_disconnected()
                if self._stop_event.is_set():
                    break
                if not await self._attempt_connect():
                    await self._cooldown_after_failure()
                continue

            if event is None:
                continue

            if self._stop_event.is_set() or self.queue is None:
                break

            await self._enqueue(event)

    async def handle_payload(self, payload: Any) -> None:
        """Process a decoded event from the connector."""
        if not isinstance(payload, dict):
            return

        event_type = payload.get("type", "").lower()

        if event_type == "trade":
            await self._handle_trade_event(payload)
        elif event_type == "depth":
            await self._handle_depth_event(payload)

    async def _handle_trade_event(self, payload: dict[str, Any]) -> None:
        """Process a trade event from the connector."""
        try:
            tick = self._parse_connector_trade(payload)
        except (ValueError, KeyError) as exc:
            structured_log(
                self.logger,
                "connector_trade_parse_error",
                error=str(exc),
                connector="hft",
            )
            return

        self.state.last_ts = tick.ts
        self.metrics.record_trade()

        # Forward to context service if available
        if self.context_service:
            self.context_service.ingest_trade(tick)

        # Forward to strategy engine if available
        if self._strategy_engine:
            self._strategy_engine.ingest_trade(tick)

        lag_ms = (datetime.now(timezone.utc) - tick.ts).total_seconds() * 1000
        structured_log(
            self.logger,
            "connector_trade",
            price=tick.price,
            qty=tick.qty,
            side=tick.side.value,
            lag_ms=round(lag_ms, 2),
            queue_size=self.queue_size,
            connector="hft",
        )

    async def _handle_depth_event(self, payload: dict[str, Any]) -> None:
        """Process a depth event from the connector."""
        try:
            update = self._parse_connector_depth(payload)
        except (ValueError, KeyError) as exc:
            structured_log(
                self.logger,
                "connector_depth_parse_error",
                error=str(exc),
                connector="hft",
            )
            return

        if update is None:
            return

        self.state.last_ts = update.ts
        self.metrics.record_depth()

        lag_ms = (datetime.now(timezone.utc) - update.ts).total_seconds() * 1000
        structured_log(
            self.logger,
            "connector_depth",
            lag_ms=round(lag_ms, 2),
            queue_size=self.queue_size,
            bids=len(update.bids),
            asks=len(update.asks),
            connector="hft",
        )

    @staticmethod
    def _parse_connector_trade(payload: dict[str, Any]) -> TradeTick:
        """Parse a trade event into TradeTick model.
        
        Expected payload format:
        {
            'type': 'trade',
            'timestamp': datetime or int (ms),
            'price': float,
            'qty': float,
            'side': 'buy' or 'sell',
            'is_buyer_maker': bool,
            'id': int
        }
        """
        timestamp = payload.get("timestamp")
        if timestamp is None:
            raise ValueError("trade payload missing timestamp")

        # Handle both datetime and millisecond timestamp
        if isinstance(timestamp, datetime):
            ts = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        elif isinstance(timestamp, (int, float)):
            ts = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        else:
            raise ValueError("invalid timestamp type")

        price = float(payload.get("price", 0))
        qty = float(payload.get("qty", 0))
        side_str = str(payload.get("side", "")).lower()
        is_buyer_maker = bool(payload.get("is_buyer_maker", False))
        trade_id = int(payload.get("id", 0))

        if side_str not in {"buy", "sell"}:
            raise ValueError(f"invalid trade side: {side_str}")

        side = TradeSide.BUY if side_str == "buy" else TradeSide.SELL

        return TradeTick(
            ts=ts,
            price=price,
            qty=qty,
            side=side,
            isBuyerMaker=is_buyer_maker,
            id=trade_id,
        )

    @staticmethod
    def _parse_connector_depth(payload: dict[str, Any]) -> Optional[DepthUpdate]:
        """Parse a depth event into DepthUpdate model.
        
        Expected payload format:
        {
            'type': 'depth',
            'timestamp': datetime or int (ms),
            'bids': [(price, qty), ...],
            'asks': [(price, qty), ...],
            'last_update_id': int
        }
        """
        timestamp = payload.get("timestamp")
        if timestamp is None:
            raise ValueError("depth payload missing timestamp")

        # Handle both datetime and millisecond timestamp
        if isinstance(timestamp, datetime):
            ts = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        elif isinstance(timestamp, (int, float)):
            ts = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        else:
            raise ValueError("invalid timestamp type")

        bids_data = payload.get("bids", [])
        asks_data = payload.get("asks", [])
        last_update_id = int(payload.get("last_update_id", 0))

        # Convert bid/ask data to PriceLevel objects
        bids = [
            PriceLevel(price=float(p), qty=float(q)) for p, q in bids_data
        ]
        asks = [
            PriceLevel(price=float(p), qty=float(q)) for p, q in asks_data
        ]

        return DepthUpdate(
            ts=ts,
            bids=bids,
            asks=asks,
            lastUpdateId=last_update_id,
        )

    def get_health_status(self) -> dict[str, Any]:
        """Get health status of the connector stream."""
        connector_health = self.connector.get_health_status()
        return {
            "connected": self.state.connected,
            "last_ts": self.state.last_ts.isoformat() if self.state.last_ts else None,
            "queue_size": self.queue_size,
            "reconnection_attempts": self._reconnection_attempts,
            "connector_health": connector_health,
        }


class StubbedConnector(ConnectorWrapper):
    """Stubbed connector implementation for testing and development.
    
    Simulates a real exchange connector by generating synthetic trade and depth events.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the stubbed connector.
        
        Args:
            settings: Application settings
        """
        self.settings = settings
        self.logger = logging.getLogger("data_sources.stubbed_connector")
        self._connected = False
        self._subscribed_trades = False
        self._subscribed_depth = False
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._generator_task: Optional[asyncio.Task] = None
        self._event_counter = 0
        self._health_check_count = 0

    async def connect(self) -> None:
        """Connect to the stubbed connector."""
        if self._connected:
            return

        self._connected = True
        self._health_check_count = 0
        self._generator_task = asyncio.create_task(self._generate_events())
        structured_log(
            self.logger,
            "stubbed_connector_connected",
            symbol=self.settings.symbol,
        )

    async def disconnect(self) -> None:
        """Disconnect from the stubbed connector."""
        if not self._connected:
            return

        self._connected = False
        if self._generator_task:
            self._generator_task.cancel()
            try:
                await self._generator_task
            except asyncio.CancelledError:
                pass
            self._generator_task = None

        structured_log(
            self.logger,
            "stubbed_connector_disconnected",
        )

    async def subscribe_trades(self, symbol: str) -> None:
        """Subscribe to trade updates for a symbol."""
        self._subscribed_trades = True
        structured_log(
            self.logger,
            "stubbed_connector_subscribed_trades",
            symbol=symbol,
        )

    async def subscribe_depth(self, symbol: str) -> None:
        """Subscribe to depth/L2 book updates for a symbol."""
        self._subscribed_depth = True
        structured_log(
            self.logger,
            "stubbed_connector_subscribed_depth",
            symbol=symbol,
        )

    async def next_event(self) -> Optional[dict[str, Any]]:
        """Get the next event from the connector.
        
        Returns None if no event is available within the timeout.
        """
        try:
            event = await asyncio.wait_for(self._event_queue.get(), timeout=0.1)
            return event
        except asyncio.TimeoutError:
            return None

    async def is_connected(self) -> bool:
        """Check if the connector is currently connected."""
        return self._connected

    def get_health_status(self) -> dict[str, Any]:
        """Get connector health status."""
        return {
            "connected": self._connected,
            "subscribed_trades": self._subscribed_trades,
            "subscribed_depth": self._subscribed_depth,
            "event_counter": self._event_counter,
        }

    async def _generate_events(self) -> None:
        """Generate synthetic trade and depth events."""
        import random

        base_price = 100.0
        price = base_price

        try:
            while self._connected:
                # Generate trade events
                if self._subscribed_trades and random.random() < 0.6:
                    price_change = random.uniform(-0.5, 0.5)
                    price = max(base_price * 0.99, min(base_price * 1.01, price + price_change))
                    qty = random.uniform(0.1, 1.0)
                    is_buyer_maker = random.choice([True, False])

                    trade_event = {
                        "type": "trade",
                        "timestamp": datetime.now(timezone.utc),
                        "price": price,
                        "qty": qty,
                        "side": "buy" if not is_buyer_maker else "sell",
                        "is_buyer_maker": is_buyer_maker,
                        "id": self._event_counter,
                    }
                    self._event_counter += 1
                    await self._event_queue.put(trade_event)

                # Generate depth events
                if self._subscribed_depth and random.random() < 0.3:
                    bids = [
                        (price - 0.1, random.uniform(1.0, 5.0)),
                        (price - 0.2, random.uniform(1.0, 5.0)),
                        (price - 0.3, random.uniform(1.0, 5.0)),
                    ]
                    asks = [
                        (price + 0.1, random.uniform(1.0, 5.0)),
                        (price + 0.2, random.uniform(1.0, 5.0)),
                        (price + 0.3, random.uniform(1.0, 5.0)),
                    ]

                    depth_event = {
                        "type": "depth",
                        "timestamp": datetime.now(timezone.utc),
                        "bids": bids,
                        "asks": asks,
                        "last_update_id": self._event_counter,
                    }
                    self._event_counter += 1
                    await self._event_queue.put(depth_event)

                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
