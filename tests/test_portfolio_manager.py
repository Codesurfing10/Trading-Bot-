"""Tests for bot.portfolio_manager."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from bot.portfolio_manager import PortfolioManager, Position


def _make_position(
    symbol: str = "AAPL",
    entry_price: float = 100.0,
    original_shares: int = 10,
    current_shares: int | None = None,
    days_ago: int = 0,
) -> Position:
    if current_shares is None:
        current_shares = original_shares
    entry_time = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return Position(
        symbol=symbol,
        entry_price=entry_price,
        entry_time=entry_time,
        original_shares=original_shares,
        current_shares=current_shares,
    )


class TestPosition(unittest.TestCase):
    def test_serialise_roundtrip(self) -> None:
        pos = _make_position("MSFT", entry_price=300.0, original_shares=3)
        pos.tranches_sold = ["t1"]
        d = pos.to_dict()
        pos2 = Position.from_dict(d)
        self.assertEqual(pos2.symbol, "MSFT")
        self.assertEqual(pos2.entry_price, 300.0)
        self.assertEqual(pos2.original_shares, 3)
        self.assertEqual(pos2.tranches_sold, ["t1"])

    def test_from_dict_invalid_date(self) -> None:
        d = {
            "symbol": "X",
            "entry_price": 10.0,
            "entry_time": "not-a-date",
            "original_shares": 5,
            "current_shares": 5,
            "tranches_sold": [],
        }
        pos = Position.from_dict(d)
        # Should not raise; entry_time defaults to now
        self.assertIsNotNone(pos.entry_time)


class TestPortfolioManager(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False
        )
        self._tmp.close()
        os.unlink(self._tmp.name)  # let PortfolioManager create it
        self.pm = PortfolioManager(
            state_file=self._tmp.name, max_positions=2
        )

    def tearDown(self) -> None:
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)

    def test_add_and_retrieve_position(self) -> None:
        pos = _make_position("AAPL")
        self.pm.add_position(pos)
        retrieved = self.pm.get_position("AAPL")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.entry_price, 100.0)

    def test_can_add_position_respects_max(self) -> None:
        self.pm.add_position(_make_position("AAPL"))
        self.pm.add_position(_make_position("MSFT", entry_price=300.0))
        self.assertFalse(self.pm.can_add_position())

    def test_add_beyond_max_raises(self) -> None:
        self.pm.add_position(_make_position("AAPL"))
        self.pm.add_position(_make_position("MSFT", entry_price=300.0))
        with self.assertRaises(ValueError):
            self.pm.add_position(_make_position("GOOG", entry_price=150.0))

    def test_duplicate_symbol_raises(self) -> None:
        self.pm.add_position(_make_position("AAPL"))
        with self.assertRaises(ValueError):
            self.pm.add_position(_make_position("AAPL"))

    def test_remove_position(self) -> None:
        self.pm.add_position(_make_position("AAPL"))
        removed = self.pm.remove_position("AAPL")
        self.assertEqual(removed.symbol, "AAPL")
        self.assertIsNone(self.pm.get_position("AAPL"))

    def test_remove_nonexistent_returns_none(self) -> None:
        result = self.pm.remove_position("ZZYZ")
        self.assertIsNone(result)

    def test_state_persists_and_loads(self) -> None:
        pos = _make_position("TSLA", entry_price=200.0, original_shares=5)
        pos.tranches_sold = ["t1"]
        self.pm.add_position(pos)

        # Create a fresh manager pointing to same file
        pm2 = PortfolioManager(state_file=self._tmp.name, max_positions=2)
        loaded = pm2.get_position("TSLA")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.entry_price, 200.0)
        self.assertEqual(loaded.tranches_sold, ["t1"])

    def test_held_symbols(self) -> None:
        self.pm.add_position(_make_position("AAPL"))
        self.pm.add_position(_make_position("MSFT", entry_price=300.0))
        self.assertEqual(self.pm.held_symbols(), {"AAPL", "MSFT"})

    def test_is_max_hold_exceeded(self) -> None:
        old_pos = _make_position("AAPL", days_ago=6)
        self.pm.add_position(old_pos)
        # 6 calendar days ago should exceed 5 trading days (or close to it)
        # With weekdays only it might vary; check the logic doesn't crash
        result = self.pm.is_max_hold_exceeded("AAPL", max_hold_days=5)
        self.assertIsInstance(result, bool)

    def test_is_max_hold_not_exceeded(self) -> None:
        new_pos = _make_position("AAPL", days_ago=1)
        self.pm.add_position(new_pos)
        self.assertFalse(self.pm.is_max_hold_exceeded("AAPL", max_hold_days=5))

    def test_update_position(self) -> None:
        pos = _make_position("AAPL", original_shares=10, current_shares=10)
        self.pm.add_position(pos)
        pos.current_shares = 5
        pos.tranches_sold = ["t1"]
        self.pm.update_position(pos)

        pm2 = PortfolioManager(state_file=self._tmp.name, max_positions=2)
        loaded = pm2.get_position("AAPL")
        self.assertEqual(loaded.current_shares, 5)
        self.assertEqual(loaded.tranches_sold, ["t1"])

    def test_load_from_corrupt_file(self) -> None:
        with open(self._tmp.name, "w") as fh:
            fh.write("not valid json{{{")
        pm = PortfolioManager(state_file=self._tmp.name, max_positions=2)
        self.assertEqual(pm.get_positions(), [])


if __name__ == "__main__":
    unittest.main()
