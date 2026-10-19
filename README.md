# Inquietum

A modular algorithmic trading system in Python. Strategies, risk management, and exchange connectors are kept separate, so the same strategy code runs in backtests, paper trading, and live trading.

## Features

- **Strategy:** 20/50 SMA crossover that emits BUY / SELL / HOLD signals
- **Risk manager:** position sizing based on risk per trade, hard stop-loss, and a drawdown kill switch that halts trading
- **Backtesting:** vectorbt backtests on BTC/ETH data from the Binance API, with fees and slippage modeled

## Structure

```
core/
  strategy.py        # signal generation
  risk_manager.py    # position sizing, stop-loss, kill switch
  base_connector.py  # exchange interface
  trade_logger.py    # trade logging
backtests/
  backtest_ma_crossover.py
config.py            # settings loaded from .env
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env            # then fill in your values
```

Python 3.12 is recommended (vectorbt/numba).

## Run a backtest

```bash
python -m backtests.backtest_ma_crossover
python -m backtests.backtest_ma_crossover --symbol ETHUSDT --days 730
```
