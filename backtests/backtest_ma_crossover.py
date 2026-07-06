"""
backtest_ma_crossover.py — First backtest: 20/50 SMA crossover on BTC/ETH.

Fetches 1h candles from Binance's PUBLIC klines endpoint (no API key
needed), reuses the exact same MACrossoverStrategy.compute_indicators()
the live bot uses, and runs it through vectorbt.

Run from the project root:
    python -m backtests.backtest_ma_crossover
    python -m backtests.backtest_ma_crossover --symbol ETHUSDT --days 730

Important honesty note on results: MA crossover on 1h candles is a trend
strategy — expect long flat/losing stretches in choppy regimes. The point
of this backtest is to build the *pipeline* and learn the metrics, not to
find a money printer on day one.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import vectorbt as vbt

from core.strategy import MACrossoverStrategy

BINANCE_KLINES_URL = "https://api.binance.us/api/v3/klines"  # .us for US users
MAX_PER_REQUEST = 1000


def fetch_binance_candles(
    symbol: str, interval: str, days: int
) -> pd.DataFrame:
    """Download OHLCV history from Binance public API, paginated."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    rows: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        resp = requests.get(
            BINANCE_KLINES_URL,
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "limit": MAX_PER_REQUEST,
            },
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + 1  # next ms after last open time
        time.sleep(0.25)           # stay well under rate limits

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_base", "taker_quote", "ignore",
    ])
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df.index.name = "timestamp"
    return df


def run_backtest(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    days: int = 365,
    fast: int = 20,
    slow: int = 50,
    fees: float = 0.001,      # 0.1% per side — Binance taker fee
    slippage: float = 0.0005, # 5 bps assumed slippage
    init_cash: float = 10_000.0,
) -> vbt.Portfolio:
    print(f"Fetching {days}d of {interval} candles for {symbol}...")
    candles = fetch_binance_candles(symbol, interval, days)
    print(f"Got {len(candles)} candles "
          f"({candles.index[0]} → {candles.index[-1]})")

    # Same indicator code path as the live bot — no logic drift.
    strat = MACrossoverStrategy(fast_period=fast, slow_period=slow)
    df = strat.compute_indicators(candles)

    entries = (df["fast_ma"] > df["slow_ma"]) & (
        df["fast_ma"].shift(1) <= df["slow_ma"].shift(1)
    )
    exits = (df["fast_ma"] < df["slow_ma"]) & (
        df["fast_ma"].shift(1) >= df["slow_ma"].shift(1)
    )

    pf = vbt.Portfolio.from_signals(
        close=df["close"],
        entries=entries,
        exits=exits,
        init_cash=init_cash,
        fees=fees,
        slippage=slippage,
        sl_stop=0.05,          # matches live hard stop-loss
        freq=interval,
    )

    print("\n" + "=" * 60)
    print(f"  {symbol} | SMA{fast}/{slow} | {interval} | last {days}d")
    print("=" * 60)
    stats = pf.stats()
    print(stats.to_string())

    bh_return = (candles["close"].iloc[-1] / candles["close"].iloc[0] - 1) * 100
    print(f"\nBuy & hold benchmark: {bh_return:+.2f}%")
    print("If the strategy can't beat buy & hold risk-adjusted, "
          "that's a finding, not a failure.")
    return pf


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MA crossover backtest")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--fast", type=int, default=20)
    parser.add_argument("--slow", type=int, default=50)
    args = parser.parse_args()

    portfolio = run_backtest(
        symbol=args.symbol,
        interval=args.interval,
        days=args.days,
        fast=args.fast,
        slow=args.slow,
    )
