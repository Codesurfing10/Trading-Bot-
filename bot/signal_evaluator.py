"""
Signal evaluator — maps scanner records to actionable buy/hold/skip decisions.

Buy criteria (ALL must be satisfied):
  1. action_signal == "BUY"
  2. signal_type  != "overbought"
  3. rsi < rsi_overbought threshold (default 70)
  4. confirms_stage2_volume == "Strong Volume Confirmation"

Overbought filter (ANY triggers rejection):
  - signal_type == "overbought"
  - rsi >= rsi_overbought (or rsi is None/missing)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_OVERBOUGHT_SIGNAL_TYPE = "overbought"
_REQUIRED_ACTION_SIGNAL = "BUY"
_REQUIRED_VOLUME_CONFIRM = "Strong Volume Confirmation"


def is_overbought(stock: dict[str, Any], rsi_threshold: float = 70.0) -> bool:
    """Return True if the stock should be skipped due to overbought conditions."""
    if stock.get("signal_type") == _OVERBOUGHT_SIGNAL_TYPE:
        return True
    rsi = stock.get("rsi")
    if rsi is None:
        # Missing RSI — treat as overbought to be conservative
        return True
    try:
        return float(rsi) >= rsi_threshold
    except (TypeError, ValueError):
        return True


def has_buy_signal(
    stock: dict[str, Any],
    rsi_threshold: float = 70.0,
) -> bool:
    """Return True when the stock meets all entry criteria."""
    if stock.get("action_signal") != _REQUIRED_ACTION_SIGNAL:
        return False
    if is_overbought(stock, rsi_threshold):
        return False
    if stock.get("confirms_stage2_volume") != _REQUIRED_VOLUME_CONFIRM:
        return False
    return True


def get_buy_candidates(
    stocks: list[dict[str, Any]],
    rsi_threshold: float = 70.0,
    exclude_symbols: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter *stocks* down to valid buy candidates, ranked by signal strength.

    Args:
        stocks:          Raw list of stock records from the scanner.
        rsi_threshold:   RSI above which stocks are considered overbought.
        exclude_symbols: Symbols already held (will not be re-entered).

    Returns:
        Filtered, sorted list of buy candidates (best first).
        Sort key: oversold > bullish/momentum > neutral; within each bucket,
        lower RSI is preferred.
    """
    exclude = exclude_symbols or set()
    candidates: list[dict[str, Any]] = []

    for stock in stocks:
        sym = stock.get("symbol", "")
        if sym in exclude:
            logger.debug("Skipping %s — already in portfolio", sym)
            continue
        if has_buy_signal(stock, rsi_threshold):
            candidates.append(stock)
        else:
            logger.debug(
                "Skipping %s — action=%s signal_type=%s rsi=%s volume_confirm=%s",
                sym,
                stock.get("action_signal"),
                stock.get("signal_type"),
                stock.get("rsi"),
                stock.get("confirms_stage2_volume"),
            )

    # Sort: prefer oversold first, then bullish; within each tier prefer lower RSI
    _order = {"oversold": 0, "bullish": 1, "neutral": 2, "bearish": 3}

    def _key(s: dict[str, Any]) -> tuple[int, float]:
        tier = _order.get(s.get("signal_type", "neutral"), 2)
        rsi_val = s.get("rsi") or 999.0
        try:
            rsi_val = float(rsi_val)
        except (TypeError, ValueError):
            rsi_val = 999.0
        return (tier, rsi_val)

    candidates.sort(key=_key)
    logger.info(
        "Signal evaluator: %d candidate(s) from %d stock(s) scanned",
        len(candidates),
        len(stocks),
    )
    return candidates
