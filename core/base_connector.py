"""
base_connector.py — Abstract market connector interface.

The core engine (strategy, risk manager, bot loop) only ever talks to this
interface. It has no idea whether it's trading CME micro futures or crypto.
Swapping markets = swapping the connector, nothing else.

Every concrete connector (FuturesConnector, CryptoConnector) MUST implement
all abstract methods and return the shared dataclasses defined here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Shared domain types — the common language between core engine and connectors
# ---------------------------------------------------------------------------

class Side(str, Enum):
    """Order direction."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Supported order types (keep minimal until strategy needs more)."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(str, Enum):
    """Normalized order lifecycle states across all brokers."""
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Order:
    """A normalized order result returned by any connector."""
    order_id: str
    symbol: str
    side: Side
    qty: float
    order_type: OrderType
    status: OrderStatus
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True)
class Position:
    """A normalized open position."""
    symbol: str
    qty: float                 # positive = long, negative = short, 0 = flat
    avg_entry_price: float
    unrealized_pnl: float = 0.0

    @property
    def is_flat(self) -> bool:
        return self.qty == 0

    @property
    def is_long(self) -> bool:
        return self.qty > 0

    @property
    def is_short(self) -> bool:
        return self.qty < 0


@dataclass(frozen=True)
class AccountBalance:
    """Normalized account state used by the risk manager."""
    total_equity: float        # cash + unrealized P&L (mark-to-market)
    available_margin: float    # what's actually deployable right now
    currency: str = "USD"


# ---------------------------------------------------------------------------
# The abstract connector
# ---------------------------------------------------------------------------

class BaseConnector(ABC):
    """
    Abstract interface every market connector must implement.

    Contract rules:
      * get_candles() returns a pandas DataFrame indexed by UTC timestamp
        with columns: open, high, low, close, volume (all float).
      * All prices are in the account currency (USD).
      * All methods raise ConnectorError (or a subclass) on failure —
        never broker-specific exceptions. The core engine catches one type.
    """

    #: Human-readable connector name, used in logs and Telegram alerts.
    name: str = "base"

    # -- market data --------------------------------------------------------

    @abstractmethod
    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
    ) -> pd.DataFrame:
        """
        Fetch the most recent OHLCV candles.

        Args:
            symbol: Market symbol in the connector's native format
                    (e.g. "BTCUSDT" for Binance, "MESU6" for Tradovate).
            timeframe: Candle interval, e.g. "1h", "4h", "1d".
            limit: Number of candles to return (most recent last).

        Returns:
            DataFrame indexed by UTC DatetimeIndex with columns
            [open, high, low, close, volume], oldest row first.
        """
        raise NotImplementedError

    # -- trading ------------------------------------------------------------

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: Side,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Order:
        """
        Submit an order and return its normalized result.

        Args:
            symbol: Market symbol.
            side: Side.BUY or Side.SELL.
            qty: Quantity in the connector's native unit
                 (contracts for futures, base asset for crypto).
            order_type: MARKET / LIMIT / STOP.
            price: Required for LIMIT and STOP orders, ignored for MARKET.
        """
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an open order. Returns True if cancelled."""
        raise NotImplementedError

    # -- account state ------------------------------------------------------

    @abstractmethod
    def get_position(self, symbol: str) -> Position:
        """Return the current position for a symbol (flat if none)."""
        raise NotImplementedError

    @abstractmethod
    def get_account_balance(self) -> AccountBalance:
        """Return current account equity and available margin."""
        raise NotImplementedError

    # -- lifecycle ----------------------------------------------------------

    def connect(self) -> None:
        """Optional: open sessions / authenticate. Default is a no-op."""

    def close(self) -> None:
        """Optional: cleanly close sessions/websockets. Default is a no-op."""

    def __enter__(self) -> "BaseConnector":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ConnectorError(Exception):
    """Base error for all connector failures. Core engine catches this."""


class AuthenticationError(ConnectorError):
    """API keys invalid or expired."""


class InsufficientFundsError(ConnectorError):
    """Not enough margin/balance to place the order."""


class MarketDataError(ConnectorError):
    """Candle fetch failed or returned malformed data."""
