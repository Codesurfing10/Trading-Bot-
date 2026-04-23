"""
Risk manager — position sizing and exit-condition logic.

Take-profit tranche schedule (fractions of *original* share count):
  T1 (+5 %):  sell 50 % of original shares
  T2 (+10 %): sell 25 % of original shares
  T3 (+25 %): sell 25 % of original shares
  Hard exit (+30 %): close any remaining shares (catch-all)

Stop loss: −2.5 % from entry price (triggers immediate market-order exit).
Time stop: exit all remaining shares after max_hold_days trading days.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# Tranche identifiers
TRANCHE_1 = "t1"
TRANCHE_2 = "t2"
TRANCHE_3 = "t3"
TRANCHE_HARD = "hard"
TRANCHE_TIME = "time"
TRANCHE_STOP = "stop"


def compute_shares(price: float, block_size: float = 1000.0) -> int:
    """Return the number of whole shares purchasable with *block_size* USD.

    Uses floor division so the notional never exceeds *block_size*.

    Args:
        price:      Ask/last price per share.
        block_size: Target notional in USD (default $1 000).

    Returns:
        Integer share count (≥ 0).  Returns 0 if *price* ≤ 0.
    """
    if price <= 0:
        return 0
    return math.floor(block_size / price)


def pct_gain(current_price: float, entry_price: float) -> float:
    """Return percentage gain (positive = profit, negative = loss).

    Returns 0.0 if *entry_price* is 0 to avoid division by zero.
    """
    if entry_price == 0:
        return 0.0
    return (current_price - entry_price) / entry_price * 100.0


def check_stop_loss(
    current_price: float,
    entry_price: float,
    stop_loss_pct: float = 2.5,
) -> bool:
    """Return True when the position has hit the stop-loss level."""
    return pct_gain(current_price, entry_price) <= -abs(stop_loss_pct)


def check_take_profit_tranche(
    current_price: float,
    entry_price: float,
    tranches_sold: set[str],
    tranche_1_pct: float = 5.0,
    tranche_2_pct: float = 10.0,
    tranche_3_pct: float = 25.0,
    hard_exit_pct: float = 30.0,
) -> Optional[str]:
    """Return the *next* take-profit tranche to execute, or ``None``.

    Tranches are evaluated highest-priority first so only one is returned per
    call; the caller re-evaluates on the next cycle.

    Returns:
        TRANCHE_HARD, TRANCHE_3, TRANCHE_2, TRANCHE_1, or None.
    """
    gain = pct_gain(current_price, entry_price)

    if gain >= hard_exit_pct and TRANCHE_HARD not in tranches_sold:
        return TRANCHE_HARD
    if gain >= tranche_3_pct and TRANCHE_3 not in tranches_sold:
        return TRANCHE_3
    if gain >= tranche_2_pct and TRANCHE_2 not in tranches_sold:
        return TRANCHE_2
    if gain >= tranche_1_pct and TRANCHE_1 not in tranches_sold:
        return TRANCHE_1
    return None


def shares_to_sell_tranche(
    original_shares: int,
    current_shares: int,
    tranche: str,
    tranche_1_fraction: float = 0.50,
    tranche_2_fraction: float = 0.25,
    tranche_3_fraction: float = 0.25,
) -> int:
    """Return the number of shares to sell for *tranche*.

    For TRANCHE_HARD, TRANCHE_TIME, and TRANCHE_STOP the entire remaining
    position is closed (*current_shares*).  For numbered tranches the sell
    quantity is derived from *original_shares* so that partial execution of
    earlier tranches does not compound errors.  The quantity is capped at
    *current_shares* to avoid overselling.

    Args:
        original_shares: Total shares bought at entry.
        current_shares:  Shares still held.
        tranche:         One of the TRANCHE_* constants.

    Returns:
        Integer share count to sell (≥ 1, capped at *current_shares*).
    """
    if tranche in (TRANCHE_HARD, TRANCHE_TIME, TRANCHE_STOP):
        return current_shares

    if tranche == TRANCHE_1:
        qty = math.floor(original_shares * tranche_1_fraction)
    elif tranche == TRANCHE_2:
        qty = math.floor(original_shares * tranche_2_fraction)
    elif tranche == TRANCHE_3:
        qty = math.floor(original_shares * tranche_3_fraction)
    else:
        logger.warning("Unknown tranche '%s'; selling entire position", tranche)
        return current_shares

    # Ensure at least 1 share and never exceed what we hold
    qty = max(1, qty)
    return min(qty, current_shares)
