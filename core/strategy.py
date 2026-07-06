"""
strategy.py — Signal generation. Completely asset-agnostic.

A Strategy consumes a candles DataFrame (the format guaranteed by
BaseConnector.get_candles) and emits a Signal. It never touches an API,
never sizes a position, never places an order. That separation is what
makes the same strategy backtestable, paper-tradeable, and live-tradeable
without changing a line.

Phase 1 strategy: 20/50 SMA crossover on 1h candles.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Signal(str, Enum):
    """What the strategy wants to happen right now."""
    BUY = "BUY"      # go long (or close short and go long)
    SELL = "SELL"    # exit long (or go short, if strategy supports it)
    HOLD = "HOLD"    # do nothing


@dataclass(frozen=True)
class StrategyResult:
    """Signal plus context, so logs and alerts can explain *why*."""
    signal: Signal
    reason: str
    fast_ma: float | None = None
    slow_ma: float | None = None
    close: float | None = None


class BaseStrategy(ABC):
    """All strategies implement evaluate(). Regime switching (month 8+)
    becomes a meta-strategy that owns several of these and delegates."""

    name: str = "base"

    @abstractmethod
    def evaluate(self, candles: pd.DataFrame) -> StrategyResult:
        """
        Args:
            candles: DataFrame with columns [open, high, low, close, volume],
                     UTC DatetimeIndex, oldest first. The LAST row must be a
                     CLOSED candle — never pass a still-forming candle, or the
                     signal will repaint.
        """
        raise NotImplementedError


class MACrossoverStrategy(BaseStrategy):
    """
    Classic moving-average crossover.

      * fast SMA crosses ABOVE slow SMA  -> BUY
      * fast SMA crosses BELOW slow SMA  -> SELL
      * otherwise                        -> HOLD

    A crossover is detected by comparing the relationship on the last two
    closed candles, so the signal fires exactly once per cross.
    """

    name = "ma_crossover"

    def __init__(self, fast_period: int = 20, slow_period: int = 50) -> None:
        if fast_period >= slow_period:
            raise ValueError(
                f"fast_period ({fast_period}) must be < slow_period "
                f"({slow_period})"
            )
        self.fast_period = fast_period
        self.slow_period = slow_period

    # -- indicator computation (also reused by the backtester) --------------

    def compute_indicators(self, candles: pd.DataFrame) -> pd.DataFrame:
        """Return a copy of candles with fast_ma / slow_ma columns added."""
        df = candles.copy()
        df["fast_ma"] = df["close"].rolling(self.fast_period).mean()
        df["slow_ma"] = df["close"].rolling(self.slow_period).mean()
        return df

    # -- live evaluation -----------------------------------------------------

    def evaluate(self, candles: pd.DataFrame) -> StrategyResult:
        min_rows = self.slow_period + 1  # need previous candle for cross check
        if len(candles) < min_rows:
            return StrategyResult(
                signal=Signal.HOLD,
                reason=(
                    f"Insufficient history: {len(candles)} candles, "
                    f"need {min_rows}"
                ),
            )

        df = self.compute_indicators(candles)

        prev, curr = df.iloc[-2], df.iloc[-1]

        crossed_up = (
            prev["fast_ma"] <= prev["slow_ma"]
            and curr["fast_ma"] > curr["slow_ma"]
        )
        crossed_down = (
            prev["fast_ma"] >= prev["slow_ma"]
            and curr["fast_ma"] < curr["slow_ma"]
        )

        if crossed_up:
            signal, reason = Signal.BUY, (
                f"SMA{self.fast_period} crossed above SMA{self.slow_period}"
            )
        elif crossed_down:
            signal, reason = Signal.SELL, (
                f"SMA{self.fast_period} crossed below SMA{self.slow_period}"
            )
        else:
            trend = "above" if curr["fast_ma"] > curr["slow_ma"] else "below"
            signal, reason = Signal.HOLD, (
                f"No cross (fast MA {trend} slow MA)"
            )

        return StrategyResult(
            signal=signal,
            reason=reason,
            fast_ma=round(float(curr["fast_ma"]), 4),
            slow_ma=round(float(curr["slow_ma"]), 4),
            close=round(float(curr["close"]), 4),
        )
