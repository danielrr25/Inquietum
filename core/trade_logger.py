"""
trade_logger.py — Every trade to CSV, every event to Telegram.

Two responsibilities, deliberately kept dumb and reliable:
  1. Append-only CSV log of every executed trade (the dashboard reads this).
  2. Telegram alerts for trades, kill-switch trips, and errors.

Design notes:
  * Telegram failures NEVER crash the bot — an alert outage must not stop
    trading logic (or worse, leave a position unmanaged). Failures are
    logged locally instead.
  * CSV is append-only with a fixed schema. If the schema ever changes,
    bump TRADES_CSV filename (trades_v2.csv) rather than mutating history.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

CSV_COLUMNS = [
    "timestamp",
    "mode",           # paper / live
    "connector",      # crypto / futures
    "symbol",
    "side",           # BUY / SELL
    "qty",
    "price",
    "stop_loss",
    "reason",         # strategy's explanation
    "order_id",
    "equity_after",
]


@dataclass(frozen=True)
class TradeRecord:
    """One row in trades.csv."""
    mode: str
    connector: str
    symbol: str
    side: str
    qty: float
    price: float
    stop_loss: float
    reason: str
    order_id: str
    equity_after: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class TradeLogger:
    def __init__(
        self,
        csv_path: Path,
        telegram_token: str = "",
        telegram_chat_id: str = "",
    ) -> None:
        self.csv_path = csv_path
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self._ensure_csv()

    # ----------------------------------------------------------------- CSV

    def _ensure_csv(self) -> None:
        """Create the CSV with a header row if it doesn't exist yet."""
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.csv_path.exists():
            with self.csv_path.open("w", newline="") as f:
                csv.writer(f).writerow(CSV_COLUMNS)

    def log_trade(self, record: TradeRecord) -> None:
        """Append a trade to the CSV and fire a Telegram alert."""
        row = asdict(record)
        with self.csv_path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerow(row)
        logger.info("Trade logged: %s %s %s @ %s",
                    record.side, record.qty, record.symbol, record.price)

        emoji = "🟢" if record.side == "BUY" else "🔴"
        self.send_alert(
            f"{emoji} {record.side} {record.qty:g} {record.symbol} "
            f"@ {record.price:g}\n"
            f"Stop: {record.stop_loss:g} | Mode: {record.mode}\n"
            f"Reason: {record.reason}\n"
            f"Equity: {record.equity_after:,.2f}"
        )

    # ------------------------------------------------------------- Telegram

    def send_alert(self, message: str) -> bool:
        """
        Send a Telegram message. Returns True on success.
        Never raises — alerting must not break trading.
        """
        if not self.telegram_token or not self.telegram_chat_id:
            logger.debug("Telegram not configured, skipping alert: %s", message)
            return False
        try:
            resp = requests.post(
                TELEGRAM_API.format(token=self.telegram_token),
                json={"chat_id": self.telegram_chat_id, "text": message},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error("Telegram alert failed (%s): %s",
                             resp.status_code, resp.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            logger.error("Telegram alert exception: %s", exc)
            return False

    def alert_kill_switch(self, detail: str) -> None:
        self.send_alert(f"🛑 KILL SWITCH TRIPPED 🛑\n{detail}\n"
                        f"Trading halted. Manual reset required.")

    def alert_error(self, detail: str) -> None:
        self.send_alert(f"⚠️ Bot error:\n{detail}")
