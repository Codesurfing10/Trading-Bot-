"""
Portfolio manager — tracks open positions and persists state to a JSON file.

State file schema:
  {
    "positions": {
      "<SYMBOL>": {
        "symbol":          str,
        "entry_price":     float,
        "entry_time":      ISO-8601 string,
        "original_shares": int,
        "current_shares":  int,
        "tranches_sold":   list[str]
      },
      ...
    }
  }
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position dataclass
# ---------------------------------------------------------------------------


class Position:
    """Represents a single open stock position."""

    __slots__ = (
        "symbol",
        "entry_price",
        "entry_time",
        "original_shares",
        "current_shares",
        "tranches_sold",
    )

    def __init__(
        self,
        symbol: str,
        entry_price: float,
        entry_time: datetime,
        original_shares: int,
        current_shares: int,
        tranches_sold: list[str] | None = None,
    ) -> None:
        self.symbol = symbol
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.original_shares = original_shares
        self.current_shares = current_shares
        self.tranches_sold: list[str] = tranches_sold or []

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat(),
            "original_shares": self.original_shares,
            "current_shares": self.current_shares,
            "tranches_sold": list(self.tranches_sold),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Position":
        entry_time_raw = data.get("entry_time", "")
        try:
            entry_time = datetime.fromisoformat(entry_time_raw)
        except (ValueError, TypeError):
            entry_time = datetime.now(timezone.utc)
        return cls(
            symbol=data["symbol"],
            entry_price=float(data["entry_price"]),
            entry_time=entry_time,
            original_shares=int(data["original_shares"]),
            current_shares=int(data["current_shares"]),
            tranches_sold=list(data.get("tranches_sold", [])),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Position(symbol={self.symbol!r}, entry_price={self.entry_price}, "
            f"current_shares={self.current_shares}, tranches={self.tranches_sold})"
        )


# ---------------------------------------------------------------------------
# Portfolio manager
# ---------------------------------------------------------------------------


class PortfolioManager:
    """Manages open positions and persists state between bot restarts."""

    def __init__(self, state_file: str, max_positions: int = 2) -> None:
        self._state_file = state_file
        self._max_positions = max_positions
        self._positions: dict[str, Position] = {}
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load positions from the state file (no-op if file does not exist)."""
        if not os.path.exists(self._state_file):
            logger.debug("State file %s not found; starting with empty portfolio", self._state_file)
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            raw = data.get("positions", {})
            self._positions = {
                sym: Position.from_dict(pos)
                for sym, pos in raw.items()
            }
            logger.info("Loaded %d open position(s) from %s", len(self._positions), self._state_file)
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error("Failed to load state file %s: %s", self._state_file, exc)
            self._positions = {}

    def save(self) -> None:
        """Persist current positions to the state file."""
        data = {
            "positions": {sym: pos.to_dict() for sym, pos in self._positions.items()}
        }
        try:
            with open(self._state_file, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            logger.debug("State saved to %s (%d position(s))", self._state_file, len(self._positions))
        except OSError as exc:
            logger.error("Failed to save state file %s: %s", self._state_file, exc)

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def add_position(self, position: Position) -> None:
        """Add a new position.  Raises ValueError if at capacity."""
        if position.symbol in self._positions:
            raise ValueError(f"Position for {position.symbol} already exists")
        if len(self._positions) >= self._max_positions:
            raise ValueError(
                f"Cannot add {position.symbol}: at max capacity "
                f"({self._max_positions} positions)"
            )
        self._positions[position.symbol] = position
        logger.info(
            "Added position: %s  %d shares @ $%.2f",
            position.symbol,
            position.current_shares,
            position.entry_price,
        )
        self.save()

    def remove_position(self, symbol: str) -> Optional[Position]:
        """Remove and return the position for *symbol*, or None if not found."""
        pos = self._positions.pop(symbol, None)
        if pos:
            logger.info("Removed position: %s", symbol)
            self.save()
        return pos

    def update_position(self, position: Position) -> None:
        """Persist an updated position (e.g., after a partial sell)."""
        self._positions[position.symbol] = position
        self.save()

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def held_symbols(self) -> set[str]:
        return set(self._positions.keys())

    def can_add_position(self) -> bool:
        return len(self._positions) < self._max_positions

    def open_count(self) -> int:
        return len(self._positions)

    # ------------------------------------------------------------------
    # Time-stop helper
    # ------------------------------------------------------------------

    def trading_days_held(self, position: Position) -> int:
        """Return the number of trading days (Mon–Fri) since entry.

        Uses pandas business-day range as a proxy for trading days.
        Does not account for US market holidays; use a market-calendar
        library for production-grade holiday handling.
        """
        now = datetime.now(timezone.utc)
        entry = position.entry_time
        # Make both tz-aware
        if entry.tzinfo is None:
            entry = entry.replace(tzinfo=timezone.utc)
        # pandas bdate_range counts Mon–Fri business days
        bdays = pd.bdate_range(
            start=entry.date(),
            end=now.date(),
        )
        # Subtract 1: the entry day itself is day 0
        return max(0, len(bdays) - 1)

    def is_max_hold_exceeded(self, symbol: str, max_hold_days: int) -> bool:
        """Return True if *symbol* has been held longer than *max_hold_days*."""
        pos = self._positions.get(symbol)
        if pos is None:
            return False
        days = self.trading_days_held(pos)
        return days >= max_hold_days
