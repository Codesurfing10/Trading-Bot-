"""Tests for bot.scanner_client."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from bot.scanner_client import fetch_scanner_data, get_stock_list, fetch_stocks

_SAMPLE_PAYLOAD = {
    "updated": "2026-04-23T07:25:46Z",
    "count": 2,
    "stocks": [
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "price": 175.50,
            "rsi": 45.0,
            "signal_type": "bullish",
            "action_signal": "BUY",
            "signals": ["Bullish", "MACD Bullish"],
            "stage_classification": "Stage 2 (Advancing)",
            "confirms_stage2_volume": "Strong Volume Confirmation",
        },
        {
            "symbol": "TSLA",
            "name": "Tesla Inc.",
            "price": 200.0,
            "rsi": 75.0,
            "signal_type": "overbought",
            "action_signal": "HOLD",
            "signals": ["Overbought"],
            "stage_classification": "Stage 2 (Advancing)",
            "confirms_stage2_volume": "N/A",
        },
    ],
}


class TestGetStockList(unittest.TestCase):
    def test_returns_stocks_list(self) -> None:
        result = get_stock_list(_SAMPLE_PAYLOAD)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["symbol"], "AAPL")

    def test_missing_key_returns_empty(self) -> None:
        result = get_stock_list({})
        self.assertEqual(result, [])

    def test_non_list_returns_empty(self) -> None:
        result = get_stock_list({"stocks": "not-a-list"})
        self.assertEqual(result, [])


class TestFetchScannerData(unittest.TestCase):
    @patch("bot.scanner_client.requests.get")
    def test_successful_fetch(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = _SAMPLE_PAYLOAD
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = fetch_scanner_data()
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["stocks"]), 2)

    @patch("bot.scanner_client.requests.get")
    def test_invalid_json_raises(self, mock_get: MagicMock) -> None:
        import requests as _req

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError("bad json")
        mock_get.return_value = mock_response

        with self.assertRaises(ValueError):
            fetch_scanner_data()

    @patch("bot.scanner_client.time.sleep", return_value=None)
    @patch("bot.scanner_client.requests.get")
    def test_retries_on_connection_error(
        self, mock_get: MagicMock, mock_sleep: MagicMock
    ) -> None:
        import requests as _req

        mock_get.side_effect = _req.ConnectionError("timeout")
        with self.assertRaises(_req.RequestException):
            fetch_scanner_data()
        # Should have retried _MAX_RETRIES times
        self.assertEqual(mock_get.call_count, 3)


class TestFetchStocks(unittest.TestCase):
    @patch("bot.scanner_client.requests.get")
    def test_returns_stock_list_directly(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = _SAMPLE_PAYLOAD
        mock_get.return_value = mock_response

        stocks = fetch_stocks()
        self.assertIsInstance(stocks, list)
        self.assertEqual(stocks[0]["symbol"], "AAPL")


if __name__ == "__main__":
    unittest.main()
