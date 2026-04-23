"""
Scanner client — fetches and parses the Trade Scanner 2 stocks.json.

The scanner publishes a static JSON file to GitHub Pages at:
  https://codesurfing10.github.io/Trade-Scanner-2/stocks.json

Schema (relevant fields):
  {
    "updated": "ISO-8601 timestamp",
    "count":   <int>,
    "stocks": [
      {
        "symbol":                  str,
        "name":                    str,
        "price":                   float,
        "rsi":                     float | null,
        "signal_type":             "oversold"|"bullish"|"neutral"|"bearish"|"overbought",
        "action_signal":           "BUY"|"SELL"|"HOLD",
        "signals":                 list[str],
        "stage_classification":    str,
        "confirms_stage2_volume":  str,
        ...
      },
      ...
    ]
  }
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_SCANNER_URL = (
    "https://codesurfing10.github.io/Trade-Scanner-2/stocks.json"
)

# Retry settings
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0  # seconds (doubles each retry)


def fetch_scanner_data(
    url: str = DEFAULT_SCANNER_URL,
    timeout: int = 30,
) -> dict[str, Any]:
    """Fetch and return the raw scanner payload as a dict.

    Retries up to *_MAX_RETRIES* times on transient errors.

    Raises:
        requests.HTTPError: on a non-2xx response after all retries.
        requests.RequestException: on network / timeout failures after all retries.
        ValueError: if the response body is not valid JSON.
    """
    last_exc: Exception | None = None
    delay = _RETRY_BACKOFF

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            logger.warning(
                "Scanner fetch attempt %d/%d failed (%s); retrying in %.1fs …",
                attempt,
                _MAX_RETRIES,
                exc,
                delay,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
        except requests.HTTPError as exc:
            logger.error("Scanner returned HTTP error: %s", exc)
            raise
        except ValueError as exc:
            logger.error("Scanner response is not valid JSON: %s", exc)
            raise

    raise requests.RequestException(
        f"Scanner fetch failed after {_MAX_RETRIES} attempts"
    ) from last_exc


def get_stock_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the list of stock records from the scanner payload.

    Args:
        data: Raw dict returned by :func:`fetch_scanner_data`.

    Returns:
        List of stock-record dicts (may be empty).
    """
    stocks = data.get("stocks")
    if not isinstance(stocks, list):
        logger.warning(
            "Scanner payload has no 'stocks' list; got type %s", type(stocks).__name__
        )
        return []
    return stocks


def fetch_stocks(
    url: str = DEFAULT_SCANNER_URL,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """Convenience wrapper: fetch scanner data and return the stock list."""
    data = fetch_scanner_data(url=url, timeout=timeout)
    return get_stock_list(data)
