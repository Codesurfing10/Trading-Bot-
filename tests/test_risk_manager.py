"""Tests for bot.risk_manager."""
from __future__ import annotations

import unittest

from bot.risk_manager import (
    compute_shares,
    pct_gain,
    check_stop_loss,
    check_take_profit_tranche,
    shares_to_sell_tranche,
    TRANCHE_1,
    TRANCHE_2,
    TRANCHE_3,
    TRANCHE_HARD,
    TRANCHE_STOP,
    TRANCHE_TIME,
)


class TestComputeShares(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(compute_shares(100.0, 1000.0), 10)

    def test_floor_division(self) -> None:
        # 1000 / 33 = 30.3… → floor = 30
        self.assertEqual(compute_shares(33.0, 1000.0), 30)

    def test_price_exceeds_block(self) -> None:
        self.assertEqual(compute_shares(1500.0, 1000.0), 0)

    def test_zero_price(self) -> None:
        self.assertEqual(compute_shares(0.0, 1000.0), 0)

    def test_negative_price(self) -> None:
        self.assertEqual(compute_shares(-10.0, 1000.0), 0)


class TestPctGain(unittest.TestCase):
    def test_profit(self) -> None:
        self.assertAlmostEqual(pct_gain(110.0, 100.0), 10.0)

    def test_loss(self) -> None:
        self.assertAlmostEqual(pct_gain(97.5, 100.0), -2.5)

    def test_zero_entry(self) -> None:
        self.assertEqual(pct_gain(100.0, 0.0), 0.0)


class TestCheckStopLoss(unittest.TestCase):
    def test_triggered(self) -> None:
        # -2.5% at exactly threshold
        self.assertTrue(check_stop_loss(97.5, 100.0, 2.5))

    def test_triggered_below_threshold(self) -> None:
        self.assertTrue(check_stop_loss(96.0, 100.0, 2.5))

    def test_not_triggered(self) -> None:
        self.assertFalse(check_stop_loss(98.0, 100.0, 2.5))

    def test_profit_not_triggered(self) -> None:
        self.assertFalse(check_stop_loss(110.0, 100.0, 2.5))


class TestCheckTakeProfitTranche(unittest.TestCase):
    def test_no_tranche_below_all(self) -> None:
        result = check_take_profit_tranche(103.0, 100.0, set())
        self.assertIsNone(result)

    def test_tranche_1_triggered(self) -> None:
        result = check_take_profit_tranche(105.0, 100.0, set())
        self.assertEqual(result, TRANCHE_1)

    def test_tranche_1_already_sold(self) -> None:
        result = check_take_profit_tranche(107.0, 100.0, {TRANCHE_1})
        self.assertIsNone(result)

    def test_tranche_2_triggered(self) -> None:
        result = check_take_profit_tranche(110.0, 100.0, {TRANCHE_1})
        self.assertEqual(result, TRANCHE_2)

    def test_tranche_3_triggered(self) -> None:
        result = check_take_profit_tranche(125.0, 100.0, {TRANCHE_1, TRANCHE_2})
        self.assertEqual(result, TRANCHE_3)

    def test_hard_exit_triggered(self) -> None:
        result = check_take_profit_tranche(130.0, 100.0, {TRANCHE_1, TRANCHE_2, TRANCHE_3})
        self.assertEqual(result, TRANCHE_HARD)

    def test_hard_exit_highest_priority(self) -> None:
        # At 30%, hard exit overrides any missed earlier tranches
        result = check_take_profit_tranche(130.0, 100.0, set())
        self.assertEqual(result, TRANCHE_HARD)

    def test_tranche_3_takes_priority_over_2(self) -> None:
        # At 25%, if T1 is sold, T3 should fire before T2
        result = check_take_profit_tranche(125.0, 100.0, {TRANCHE_1})
        self.assertEqual(result, TRANCHE_3)


class TestSharesToSellTranche(unittest.TestCase):
    def test_tranche_1_half(self) -> None:
        qty = shares_to_sell_tranche(10, 10, TRANCHE_1)
        self.assertEqual(qty, 5)

    def test_tranche_2_quarter(self) -> None:
        qty = shares_to_sell_tranche(10, 5, TRANCHE_2)
        self.assertEqual(qty, 2)

    def test_tranche_3_quarter(self) -> None:
        qty = shares_to_sell_tranche(10, 3, TRANCHE_3)
        self.assertEqual(qty, 2)

    def test_hard_sells_all_remaining(self) -> None:
        qty = shares_to_sell_tranche(10, 3, TRANCHE_HARD)
        self.assertEqual(qty, 3)

    def test_stop_sells_all_remaining(self) -> None:
        qty = shares_to_sell_tranche(10, 10, TRANCHE_STOP)
        self.assertEqual(qty, 10)

    def test_time_sells_all_remaining(self) -> None:
        qty = shares_to_sell_tranche(10, 4, TRANCHE_TIME)
        self.assertEqual(qty, 4)

    def test_capped_at_current_shares(self) -> None:
        # Original 10 shares, only 1 left — sell no more than 1
        qty = shares_to_sell_tranche(10, 1, TRANCHE_1)
        self.assertEqual(qty, 1)

    def test_minimum_one_share(self) -> None:
        # 1 original share, 1 remaining — floor(1 * 0.5) = 0 → clamped to 1
        qty = shares_to_sell_tranche(1, 1, TRANCHE_1)
        self.assertEqual(qty, 1)


if __name__ == "__main__":
    unittest.main()
