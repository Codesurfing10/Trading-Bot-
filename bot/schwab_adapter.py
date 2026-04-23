"""
Schwab broker adapter.

Implements OAuth 2.0 authorization-code flow (PKCE-compatible) for the
Schwab Trader / Developer API, plus the REST calls needed by the bot:
  - get_quote        — latest price for a symbol
  - get_account_cash — available buying power
  - get_positions    — current broker-held positions
  - place_market_order
  - place_limit_order

Authentication flow (first run):
  1. Call get_authorization_url() and open the URL in a browser.
  2. Log in with your Schwab credentials and authorise the app.
  3. Copy the full redirect URL (e.g. https://127.0.0.1?code=…&session=…).
  4. Call exchange_code_for_tokens(redirect_url).  Tokens are persisted to
     the configured tokens file.
  5. On subsequent runs the adapter refreshes tokens automatically.

Schwab API base URLs (as of 2024):
  Auth:        https://api.schwabapi.com/v1/oauth/authorize
  Token:       https://api.schwabapi.com/v1/oauth/token
  Market data: https://api.schwabapi.com/marketdata/v1/
  Trader:      https://api.schwabapi.com/trader/v1/
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, Optional
from urllib.parse import urlencode, urlparse, parse_qs

import requests

logger = logging.getLogger(__name__)

_AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"  # noqa: S105 (not a secret)
_MARKETDATA_BASE = "https://api.schwabapi.com/marketdata/v1"
_TRADER_BASE = "https://api.schwabapi.com/trader/v1"

# Seconds before expiry to trigger a proactive token refresh
_TOKEN_REFRESH_BUFFER = 300

# Retry settings for API calls
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0


class SchwabAuthError(Exception):
    """Raised when authentication or token refresh fails."""


class SchwabAPIError(Exception):
    """Raised when the Schwab REST API returns an unexpected error."""


class SchwabAdapter:
    """Client for the Schwab Trader/Developer API."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        redirect_uri: str,
        account_hash: str,
        tokens_file: str = "tokens.json",
        dry_run: bool = False,
    ) -> None:
        if not app_key or not app_secret:
            raise ValueError("SCHWAB_APP_KEY and SCHWAB_APP_SECRET must be set")
        self._app_key = app_key
        self._app_secret = app_secret
        self._redirect_uri = redirect_uri
        self._account_hash = account_hash
        self._tokens_file = tokens_file
        self._dry_run = dry_run
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expiry: float = 0.0  # Unix timestamp
        self._load_tokens()

    # ------------------------------------------------------------------
    # Public authentication interface
    # ------------------------------------------------------------------

    def get_authorization_url(self) -> str:
        """Return the URL the user must visit to authorise the app."""
        params = {
            "response_type": "code",
            "client_id": self._app_key,
            "redirect_uri": self._redirect_uri,
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    def exchange_code_for_tokens(self, redirect_url_or_code: str) -> None:
        """Exchange the auth code (or full redirect URL) for access/refresh tokens.

        Args:
            redirect_url_or_code: Either the full redirect URL
                (``https://127.0.0.1?code=XXX&session=YYY``) or just the
                raw authorisation code string.
        """
        code = redirect_url_or_code
        if redirect_url_or_code.startswith("http"):
            parsed = urlparse(redirect_url_or_code)
            qs = parse_qs(parsed.query)
            code_list = qs.get("code")
            if not code_list:
                raise SchwabAuthError(
                    "Could not extract 'code' from redirect URL: "
                    f"{redirect_url_or_code}"
                )
            code = code_list[0]

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
        }
        self._request_tokens(payload)
        logger.info("Schwab tokens obtained and saved to %s", self._tokens_file)

    def ensure_authenticated(self) -> None:
        """Refresh the access token if it is missing or about to expire."""
        if self._access_token and time.time() < self._token_expiry - _TOKEN_REFRESH_BUFFER:
            return
        if self._refresh_token:
            self._refresh_access_token()
        else:
            raise SchwabAuthError(
                "No valid tokens found.  Run the authorisation flow first:\n"
                f"  python -m bot.main --auth"
            )

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_quote(self, symbol: str) -> dict[str, Any]:
        """Return quote data for *symbol*.

        Returns a dict with at least: ``lastPrice``, ``askPrice``, ``bidPrice``.
        """
        self.ensure_authenticated()
        url = f"{_MARKETDATA_BASE}/quotes"
        resp = self._get(url, params={"symbols": symbol})
        data = resp.json()
        # Schwab returns {SYMBOL: {"quote": {...}, "reference": {...}}}
        if symbol not in data:
            raise SchwabAPIError(f"Symbol {symbol!r} not found in quote response")
        return data[symbol].get("quote", {})

    def get_last_price(self, symbol: str) -> float:
        """Return the last traded price for *symbol*."""
        quote = self.get_quote(symbol)
        price = quote.get("lastPrice") or quote.get("askPrice")
        if price is None:
            raise SchwabAPIError(f"No price available for {symbol!r}")
        return float(price)

    # ------------------------------------------------------------------
    # Account / positions
    # ------------------------------------------------------------------

    def get_account_numbers(self) -> list[dict[str, str]]:
        """Return list of ``{accountNumber, hashValue}`` dicts."""
        self.ensure_authenticated()
        resp = self._get(f"{_TRADER_BASE}/accounts/accountNumbers")
        return resp.json()

    def get_account_cash(self) -> float:
        """Return available cash/buying-power for the configured account."""
        self.ensure_authenticated()
        resp = self._get(
            f"{_TRADER_BASE}/accounts/{self._account_hash}",
            params={"fields": "positions"},
        )
        data = resp.json()
        # Navigate to cashAndSweepBalance or similar field
        try:
            return float(
                data["securitiesAccount"]["currentBalances"][
                    "cashAndSweepBalance"
                ]
            )
        except (KeyError, TypeError, ValueError):
            pass
        try:
            return float(
                data["securitiesAccount"]["currentBalances"]["availableFunds"]
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Could not parse account cash; returning 0.0")
            return 0.0

    def get_positions(self) -> list[dict[str, Any]]:
        """Return broker-held positions for the configured account."""
        self.ensure_authenticated()
        resp = self._get(
            f"{_TRADER_BASE}/accounts/{self._account_hash}",
            params={"fields": "positions"},
        )
        data = resp.json()
        return data.get("securitiesAccount", {}).get("positions", [])

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_market_order(
        self,
        symbol: str,
        quantity: int,
        side: str,
    ) -> dict[str, Any]:
        """Place a market order.

        Args:
            symbol:   Ticker symbol (e.g. "AAPL").
            quantity: Number of shares.
            side:     "BUY" or "SELL".

        Returns:
            Response dict from the API (or a dry-run log dict).
        """
        order = _build_market_order(symbol, quantity, side.upper())
        return self._place_order(order, symbol, quantity, side, "MARKET")

    def place_limit_order(
        self,
        symbol: str,
        quantity: int,
        side: str,
        price: float,
    ) -> dict[str, Any]:
        """Place a day-limit order at *price*.

        Args:
            symbol:   Ticker symbol.
            quantity: Number of shares.
            side:     "BUY" or "SELL".
            price:    Limit price.

        Returns:
            Response dict from the API (or a dry-run log dict).
        """
        order = _build_limit_order(symbol, quantity, side.upper(), price)
        return self._place_order(order, symbol, quantity, side, f"LIMIT@{price}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _place_order(
        self,
        order: dict[str, Any],
        symbol: str,
        quantity: int,
        side: str,
        order_desc: str,
    ) -> dict[str, Any]:
        if self._dry_run:
            logger.info(
                "[DRY-RUN] Would place %s %s %d shares of %s",
                order_desc,
                side,
                quantity,
                symbol,
            )
            return {
                "dry_run": True,
                "symbol": symbol,
                "quantity": quantity,
                "side": side,
                "order_type": order_desc,
            }

        self.ensure_authenticated()
        url = f"{_TRADER_BASE}/accounts/{self._account_hash}/orders"
        resp = self._post(url, json_body=order)
        result = {}
        try:
            result = resp.json()
        except ValueError:
            pass
        logger.info(
            "Order placed: %s %d shares of %s (HTTP %d)",
            side,
            quantity,
            symbol,
            resp.status_code,
        )
        return result

    def _request_tokens(self, payload: dict[str, str]) -> None:
        """POST to the token endpoint, store and persist the result."""
        credentials = base64.b64encode(
            f"{self._app_key}:{self._app_secret}".encode()
        ).decode()
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp = requests.post(_TOKEN_URL, data=payload, headers=headers, timeout=30)
        if not resp.ok:
            raise SchwabAuthError(
                f"Token request failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token")
        expires_in = int(data.get("expires_in", 1800))
        self._token_expiry = time.time() + expires_in
        self._save_tokens(data)

    def _refresh_access_token(self) -> None:
        logger.debug("Refreshing Schwab access token …")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        try:
            self._request_tokens(payload)
            logger.debug("Access token refreshed successfully")
        except SchwabAuthError as exc:
            logger.error("Token refresh failed: %s", exc)
            # Clear tokens so the bot surfaces a clear error on next call
            self._access_token = None
            self._refresh_token = None
            raise

    def _load_tokens(self) -> None:
        if not os.path.exists(self._tokens_file):
            return
        try:
            with open(self._tokens_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._token_expiry = float(data.get("token_expiry", 0))
            logger.debug("Loaded tokens from %s", self._tokens_file)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Could not load tokens from %s: %s", self._tokens_file, exc)

    def _save_tokens(self, token_data: dict[str, Any]) -> None:
        payload = {
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "token_expiry": self._token_expiry,
        }
        try:
            with open(self._tokens_file, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            logger.debug("Tokens saved to %s", self._tokens_file)
        except OSError as exc:
            logger.warning("Could not save tokens to %s: %s", self._tokens_file, exc)

    # ------------------------------------------------------------------
    # HTTP helpers with retry
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        return self._request("GET", url, params=params)

    def _post(self, url: str, json_body: dict | None = None) -> requests.Response:
        return self._request("POST", url, json_body=json_body)

    def _request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> requests.Response:
        headers = {"Authorization": f"Bearer {self._access_token}"}
        last_exc: Exception | None = None
        delay = _RETRY_BACKOFF
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=30,
                )
                if resp.status_code == 401:
                    # Token may have just expired; refresh and retry once
                    logger.warning("HTTP 401 — refreshing token and retrying")
                    self._refresh_access_token()
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    resp = requests.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        timeout=30,
                    )
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                logger.warning(
                    "API call %s %s attempt %d/%d failed (%s); retrying in %.1fs",
                    method,
                    url,
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    delay,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(delay)
                    delay *= 2
            except requests.HTTPError as exc:
                logger.error("API %s %s HTTP error: %s", method, url, exc)
                raise SchwabAPIError(str(exc)) from exc

        raise SchwabAPIError(
            f"API call {method} {url} failed after {_MAX_RETRIES} attempts"
        ) from last_exc


# ---------------------------------------------------------------------------
# Order builder helpers
# ---------------------------------------------------------------------------


def _build_market_order(symbol: str, quantity: int, side: str) -> dict[str, Any]:
    return {
        "orderType": "MARKET",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": side,
                "quantity": quantity,
                "instrument": {
                    "symbol": symbol,
                    "assetType": "EQUITY",
                },
            }
        ],
    }


def _build_limit_order(
    symbol: str, quantity: int, side: str, price: float
) -> dict[str, Any]:
    return {
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "price": str(round(price, 2)),
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": side,
                "quantity": quantity,
                "instrument": {
                    "symbol": symbol,
                    "assetType": "EQUITY",
                },
            }
        ],
    }
