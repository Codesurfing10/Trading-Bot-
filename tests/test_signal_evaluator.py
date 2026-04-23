"""Tests for bot.signal_evaluator."""
from __future__ import annotations

import unittest

from bot.signal_evaluator import is_overbought, has_buy_signal, get_buy_candidates


def _make_stock(
    symbol: str = "TEST",
    rsi: float | None = 45.0,
    signal_type: str = "bullish",
    action_signal: str = "BUY",
    confirms_stage2_volume: str = "Strong Volume Confirmation",
) -> dict:
    return {
        "symbol": symbol,
        "price": 100.0,
        "rsi": rsi,
        "signal_type": signal_type,
        "action_signal": action_signal,
        "confirms_stage2_volume": confirms_stage2_volume,
    }


class TestIsOverbought(unittest.TestCase):
    def test_overbought_signal_type(self) -> None:
        s = _make_stock(signal_type="overbought", rsi=75.0)
        self.assertTrue(is_overbought(s))

    def test_rsi_above_threshold(self) -> None:
        s = _make_stock(signal_type="bullish", rsi=71.0)
        self.assertTrue(is_overbought(s, rsi_threshold=70.0))

    def test_rsi_exactly_at_threshold(self) -> None:
        s = _make_stock(signal_type="bullish", rsi=70.0)
        self.assertTrue(is_overbought(s, rsi_threshold=70.0))

    def test_rsi_below_threshold(self) -> None:
        s = _make_stock(signal_type="bullish", rsi=65.0)
        self.assertFalse(is_overbought(s, rsi_threshold=70.0))

    def test_missing_rsi_is_conservative(self) -> None:
        s = _make_stock(signal_type="bullish", rsi=None)
        self.assertTrue(is_overbought(s))

    def test_oversold_not_overbought(self) -> None:
        s = _make_stock(signal_type="oversold", rsi=28.0)
        self.assertFalse(is_overbought(s))


class TestHasBuySignal(unittest.TestCase):
    def test_valid_buy_candidate(self) -> None:
        s = _make_stock()
        self.assertTrue(has_buy_signal(s))

    def test_overbought_rejected(self) -> None:
        s = _make_stock(signal_type="overbought", rsi=80.0)
        self.assertFalse(has_buy_signal(s))

    def test_hold_signal_rejected(self) -> None:
        s = _make_stock(action_signal="HOLD")
        self.assertFalse(has_buy_signal(s))

    def test_sell_signal_rejected(self) -> None:
        s = _make_stock(action_signal="SELL")
        self.assertFalse(has_buy_signal(s))

    def test_no_volume_confirmation_rejected(self) -> None:
        s = _make_stock(confirms_stage2_volume="N/A")
        self.assertFalse(has_buy_signal(s))

    def test_high_rsi_rejected(self) -> None:
        s = _make_stock(rsi=72.0, signal_type="bullish")
        self.assertFalse(has_buy_signal(s, rsi_threshold=70.0))

    def test_oversold_valid_entry(self) -> None:
        s = _make_stock(signal_type="oversold", rsi=28.0, action_signal="BUY")
        self.assertTrue(has_buy_signal(s))


class TestGetBuyCandidates(unittest.TestCase):
    def setUp(self) -> None:
        self.stocks = [
            _make_stock("AAPL", rsi=45.0, signal_type="bullish"),
            _make_stock("TSLA", rsi=80.0, signal_type="overbought", action_signal="HOLD"),
            _make_stock("JNJ", rsi=28.0, signal_type="oversold"),
            _make_stock("MSFT", rsi=60.0, signal_type="bullish"),
            _make_stock("GOOG", rsi=50.0, signal_type="neutral", confirms_stage2_volume="N/A"),
        ]

    def test_filters_overbought(self) -> None:
        candidates = get_buy_candidates(self.stocks)
        symbols = [c["symbol"] for c in candidates]
        self.assertNotIn("TSLA", symbols)

    def test_filters_no_volume_confirm(self) -> None:
        candidates = get_buy_candidates(self.stocks)
        symbols = [c["symbol"] for c in candidates]
        self.assertNotIn("GOOG", symbols)

    def test_oversold_comes_first(self) -> None:
        candidates = get_buy_candidates(self.stocks)
        self.assertEqual(candidates[0]["symbol"], "JNJ")

    def test_exclude_symbols(self) -> None:
        candidates = get_buy_candidates(self.stocks, exclude_symbols={"AAPL", "JNJ"})
        symbols = [c["symbol"] for c in candidates]
        self.assertNotIn("AAPL", symbols)
        self.assertNotIn("JNJ", symbols)

    def test_empty_input(self) -> None:
        self.assertEqual(get_buy_candidates([]), [])


if __name__ == "__main__":
    unittest.main()
