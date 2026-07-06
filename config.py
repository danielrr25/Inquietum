"""
config.py — Central configuration, loaded once from .env.

Design decisions:
  * Fail fast: missing required keys raise at import of Settings.load(),
    not at 3am when the bot tries to place its first order.
  * Everything typed. No raw os.environ access anywhere else in the codebase.
  * Risk parameters live here so they're visible in one place and can't be
    silently overridden deep inside strategy code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Project root = directory containing this file.
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
TRADES_CSV = DATA_DIR / "trades.csv"


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _require(key: str) -> str:
    """Fetch a required env var or raise a clear error."""
    value = os.getenv(key)
    if not value:
        raise ConfigError(
            f"Missing required environment variable: {key}. "
            f"Add it to your .env file (see .env.example)."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _as_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got: {raw!r}") from exc


@dataclass(frozen=True)
class RiskSettings:
    """Non-negotiable risk parameters. Changing these requires a code review
    with yourself and a good reason."""
    max_risk_per_trade_pct: float   # fraction of equity risked per trade
    stop_loss_pct: float            # hard stop distance below entry
    kill_switch_drawdown_pct: float # halt everything past this drawdown
    max_open_positions: int


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for the whole bot."""

    # -- mode ---------------------------------------------------------------
    # "paper" or "live". Live requires LIVE_TRADING_CONFIRMED=yes in .env
    # as a deliberate second key — no accidental live trading.
    mode: str

    # -- market -------------------------------------------------------------
    connector: str          # "crypto" or "futures"
    symbol: str             # e.g. "BTCUSDT" or "MES"
    timeframe: str          # e.g. "1h"

    # -- API credentials ----------------------------------------------------
    binance_api_key: str
    binance_api_secret: str
    binance_tld: str        # "com" or "us"

    # -- alerts -------------------------------------------------------------
    telegram_bot_token: str
    telegram_chat_id: str

    # -- risk ---------------------------------------------------------------
    risk: RiskSettings

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        """Load and validate settings from .env (or a custom env file)."""
        load_dotenv(env_file or ROOT_DIR / ".env")

        mode = _optional("MODE", "paper").lower()
        if mode not in ("paper", "live"):
            raise ConfigError(f"MODE must be 'paper' or 'live', got {mode!r}")

        # Deliberate two-key safety: live mode needs explicit confirmation.
        if mode == "live" and _optional("LIVE_TRADING_CONFIRMED") != "yes":
            raise ConfigError(
                "MODE=live but LIVE_TRADING_CONFIRMED is not 'yes'. "
                "This is a safety interlock — set it only when you truly "
                "intend to trade real capital."
            )

        connector = _optional("CONNECTOR", "crypto").lower()
        if connector not in ("crypto", "futures"):
            raise ConfigError(
                f"CONNECTOR must be 'crypto' or 'futures', got {connector!r}"
            )

        # Binance keys are only required when using the crypto connector.
        if connector == "crypto":
            binance_key = _require("BINANCE_API_KEY")
            binance_secret = _require("BINANCE_API_SECRET")
        else:
            binance_key = _optional("BINANCE_API_KEY")
            binance_secret = _optional("BINANCE_API_SECRET")

        risk = RiskSettings(
            max_risk_per_trade_pct=_as_float("MAX_RISK_PER_TRADE_PCT", 0.01),
            stop_loss_pct=_as_float("STOP_LOSS_PCT", 0.05),
            kill_switch_drawdown_pct=_as_float("KILL_SWITCH_DRAWDOWN_PCT", 0.15),
            max_open_positions=int(_as_float("MAX_OPEN_POSITIONS", 1)),
        )

        # Sanity-check risk values so a typo (e.g. 5 instead of 0.05)
        # can't blow up the account.
        if not 0 < risk.max_risk_per_trade_pct <= 0.02:
            raise ConfigError(
                "MAX_RISK_PER_TRADE_PCT must be in (0, 0.02] — "
                "your own rules cap risk at 2% per trade."
            )
        if not 0 < risk.stop_loss_pct <= 0.10:
            raise ConfigError("STOP_LOSS_PCT must be in (0, 0.10].")
        if not 0 < risk.kill_switch_drawdown_pct <= 0.25:
            raise ConfigError("KILL_SWITCH_DRAWDOWN_PCT must be in (0, 0.25].")

        return cls(
            mode=mode,
            connector=connector,
            symbol=_optional("SYMBOL", "BTCUSDT"),
            timeframe=_optional("TIMEFRAME", "1h"),
            binance_api_key=binance_key,
            binance_api_secret=binance_secret,
            binance_tld=_optional("BINANCE_TLD", "us"),
            telegram_bot_token=_optional("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_optional("TELEGRAM_CHAT_ID"),
            risk=risk,
        )
