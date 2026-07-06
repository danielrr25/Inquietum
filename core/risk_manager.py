"""
risk_manager.py — Position sizing, stop-loss levels, and the kill switch.

The risk manager sits between the strategy and the connector. A BUY signal
is a *request*; the risk manager decides whether it becomes an order and
how big that order is. It can veto anything. Nothing bypasses it.

Rules implemented (from the project spec, non-negotiable):
  * Risk at most `max_risk_per_trade_pct` of equity per trade.
  * Hard stop-loss `stop_loss_pct` below entry.
  * Kill switch: if equity drawdown from peak exceeds
    `kill_switch_drawdown_pct`, halt all trading until manually reset.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import RiskSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradePlan:
    """The risk manager's answer to 'can I take this trade?'"""
    approved: bool
    qty: float = 0.0
    stop_loss_price: float = 0.0
    reason: str = ""


class KillSwitchTripped(Exception):
    """Raised when trading is halted. The bot loop must catch this,
    send a Telegram alert, and stop placing orders."""


class RiskManager:
    """
    Stateful risk gatekeeper.

    Peak equity is persisted to disk so a bot restart can't quietly reset
    the drawdown calculation (a classic way kill switches get defeated).
    """

    def __init__(self, settings: RiskSettings, state_file: Path) -> None:
        self.settings = settings
        self.state_file = state_file
        self._peak_equity: float = 0.0
        self._halted: bool = False
        self._load_state()

    # ------------------------------------------------------------------ state

    def _load_state(self) -> None:
        if self.state_file.exists():
            try:
                state = json.loads(self.state_file.read_text())
                self._peak_equity = float(state.get("peak_equity", 0.0))
                self._halted = bool(state.get("halted", False))
            except (json.JSONDecodeError, ValueError):
                logger.warning("Risk state file corrupt, starting fresh.")

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps({
                "peak_equity": self._peak_equity,
                "halted": self._halted,
            })
        )

    # ------------------------------------------------------------ kill switch

    @property
    def is_halted(self) -> bool:
        return self._halted

    def check_kill_switch(self, current_equity: float) -> None:
        """
        Update peak equity and trip the kill switch if drawdown exceeds
        the limit. Call this EVERY cycle, before evaluating any signal.

        Raises:
            KillSwitchTripped: if trading is (or becomes) halted.
        """
        if self._halted:
            raise KillSwitchTripped(
                "Trading is halted (kill switch previously tripped). "
                "Manual reset required."
            )

        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
            self._save_state()

        if self._peak_equity <= 0:
            return  # nothing to measure against yet

        drawdown = 1.0 - (current_equity / self._peak_equity)
        if drawdown >= self.settings.kill_switch_drawdown_pct:
            self._halted = True
            self._save_state()
            raise KillSwitchTripped(
                f"KILL SWITCH: drawdown {drawdown:.1%} >= limit "
                f"{self.settings.kill_switch_drawdown_pct:.1%} "
                f"(peak {self._peak_equity:.2f}, now {current_equity:.2f})"
            )

    def manual_reset(self) -> None:
        """Deliberate, human-invoked reset after reviewing what went wrong.
        Never call this from automated code."""
        logger.warning("Kill switch manually reset. Peak equity re-anchored.")
        self._halted = False
        self._peak_equity = 0.0
        self._save_state()

    # -------------------------------------------------------------- sizing

    def plan_trade(
        self,
        equity: float,
        entry_price: float,
        open_positions: int,
        min_qty: float = 0.0,
        qty_step: Optional[float] = None,
    ) -> TradePlan:
        """
        Size a new long entry so that if the stop-loss is hit, the loss is
        at most max_risk_per_trade_pct of equity.

            risk_amount    = equity * max_risk_per_trade_pct
            stop_distance  = entry_price * stop_loss_pct
            qty            = risk_amount / stop_distance

        Args:
            equity: Current total account equity.
            entry_price: Expected fill price.
            open_positions: Positions already open (for the max cap).
            min_qty: Exchange minimum order size (0 to ignore).
            qty_step: Exchange quantity step for rounding (None to ignore).
        """
        if self._halted:
            return TradePlan(approved=False, reason="Kill switch active")

        if open_positions >= self.settings.max_open_positions:
            return TradePlan(
                approved=False,
                reason=f"Max open positions ({self.settings.max_open_positions}) reached",
            )

        if entry_price <= 0 or equity <= 0:
            return TradePlan(approved=False, reason="Invalid equity or price")

        risk_amount = equity * self.settings.max_risk_per_trade_pct
        stop_distance = entry_price * self.settings.stop_loss_pct
        qty = risk_amount / stop_distance

        # Round down to the exchange's quantity step so orders don't reject.
        if qty_step:
            qty = (qty // qty_step) * qty_step

        if min_qty and qty < min_qty:
            return TradePlan(
                approved=False,
                reason=(
                    f"Risk-based size {qty:.8f} below exchange minimum "
                    f"{min_qty} — account too small for this trade"
                ),
            )

        stop_loss_price = entry_price * (1.0 - self.settings.stop_loss_pct)

        return TradePlan(
            approved=True,
            qty=qty,
            stop_loss_price=stop_loss_price,
            reason=(
                f"Risking {risk_amount:.2f} "
                f"({self.settings.max_risk_per_trade_pct:.1%} of equity) "
                f"with stop at {stop_loss_price:.2f}"
            ),
        )
