"""
Main bot loop and CLI entry point.

Usage:
  # First-time authentication
  python -m bot.main --auth

  # List accounts (to find your account hash)
  python -m bot.main --list-accounts

  # Run the bot (dry-run by default; set DRY_RUN=false to trade live)
  python -m bot.main [--config config.yaml]

  # Run one cycle immediately, then exit
  python -m bot.main --once [--config config.yaml]
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from .config import load_config, Config
from .scanner_client import fetch_stocks
from .signal_evaluator import get_buy_candidates
from .risk_manager import (
    compute_shares,
    check_stop_loss,
    check_take_profit_tranche,
    shares_to_sell_tranche,
    TRANCHE_STOP,
    TRANCHE_TIME,
)
from .portfolio_manager import PortfolioManager, Position
from .schwab_adapter import SchwabAdapter, SchwabAuthError, SchwabAPIError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Market-hours helper (US Eastern Time, Mon–Fri, 09:30–16:00)
# ---------------------------------------------------------------------------

_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MINUTE = 30
_MARKET_CLOSE_HOUR = 16

try:
    from zoneinfo import ZoneInfo  # Python 3.9+

    _ET = ZoneInfo("America/New_York")
except ImportError:
    try:
        import pytz

        _ET = pytz.timezone("America/New_York")
    except ImportError:
        _ET = timezone.utc  # type: ignore[assignment]
        logger.warning(
            "Neither zoneinfo nor pytz available; market-hours check uses UTC "
            "(may allow orders outside US market hours)"
        )


def is_market_open() -> bool:
    """Return True during regular US market hours (Mon–Fri 09:30–16:00 ET)."""
    now = datetime.now(_ET)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now.replace(
        hour=_MARKET_OPEN_HOUR,
        minute=_MARKET_OPEN_MINUTE,
        second=0,
        microsecond=0,
    )
    market_close = now.replace(
        hour=_MARKET_CLOSE_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    return market_open <= now < market_close


# ---------------------------------------------------------------------------
# Core bot logic
# ---------------------------------------------------------------------------


def run_cycle(
    cfg: Config,
    portfolio: PortfolioManager,
    broker: SchwabAdapter,
) -> None:
    """Execute one bot cycle: check exits → check new entries."""
    logger.info("─── Starting cycle ───")

    # ── 1. Check exit conditions for all open positions ──────────────────
    for position in portfolio.get_positions():
        _process_position_exits(position, cfg, portfolio, broker)

    # ── 2. Enter new positions if capacity allows ─────────────────────────
    if not portfolio.can_add_position():
        logger.info(
            "Portfolio full (%d/%d positions); skipping new entries",
            portfolio.open_count(),
            cfg.trading.max_positions,
        )
        return

    if not is_market_open():
        logger.info("Market is closed; skipping new entry orders")
        return

    # Fetch scanner data
    try:
        stocks = fetch_stocks(
            url=cfg.scanner.url,
            timeout=cfg.scanner.timeout_seconds,
        )
    except Exception as exc:
        logger.error("Failed to fetch scanner data: %s", exc)
        return

    candidates = get_buy_candidates(
        stocks,
        rsi_threshold=cfg.trading.rsi_overbought,
        exclude_symbols=portfolio.held_symbols(),
    )

    if not candidates:
        logger.info("No buy candidates found")
        return

    slots_available = cfg.trading.max_positions - portfolio.open_count()
    for stock in candidates[:slots_available]:
        if not portfolio.can_add_position():
            break
        _enter_position(stock, cfg, portfolio, broker)


def _process_position_exits(
    position: Position,
    cfg: Config,
    portfolio: PortfolioManager,
    broker: SchwabAdapter,
) -> None:
    """Check and execute any exit orders for *position*."""
    symbol = position.symbol

    # Fetch current price
    try:
        current_price = broker.get_last_price(symbol)
    except SchwabAPIError as exc:
        logger.error("Could not get price for %s: %s", symbol, exc)
        return

    tp_cfg = cfg.take_profit
    gain_pct = (current_price - position.entry_price) / position.entry_price * 100.0
    logger.info(
        "Position %s: entry=%.2f current=%.2f gain=%.2f%% shares=%d tranches=%s",
        symbol,
        position.entry_price,
        current_price,
        gain_pct,
        position.current_shares,
        position.tranches_sold,
    )

    # ── Stop loss ─────────────────────────────────────────────────────────
    if check_stop_loss(
        current_price, position.entry_price, cfg.trading.stop_loss_pct
    ):
        logger.warning(
            "STOP LOSS triggered for %s (gain %.2f%% ≤ −%.2f%%)",
            symbol,
            gain_pct,
            cfg.trading.stop_loss_pct,
        )
        _execute_exit(position, TRANCHE_STOP, position.current_shares, broker, portfolio)
        return

    # ── Time stop ─────────────────────────────────────────────────────────
    if portfolio.is_max_hold_exceeded(symbol, cfg.trading.max_hold_days):
        logger.info(
            "TIME STOP for %s: exceeded %d trading-day max hold",
            symbol,
            cfg.trading.max_hold_days,
        )
        _execute_exit(position, TRANCHE_TIME, position.current_shares, broker, portfolio)
        return

    # ── Take-profit ladder ────────────────────────────────────────────────
    tranche = check_take_profit_tranche(
        current_price,
        position.entry_price,
        set(position.tranches_sold),
        tranche_1_pct=tp_cfg.tranche_1_pct,
        tranche_2_pct=tp_cfg.tranche_2_pct,
        tranche_3_pct=tp_cfg.tranche_3_pct,
        hard_exit_pct=tp_cfg.hard_exit_pct,
    )
    if tranche:
        qty = shares_to_sell_tranche(
            original_shares=position.original_shares,
            current_shares=position.current_shares,
            tranche=tranche,
            tranche_1_fraction=tp_cfg.tranche_1_sell_fraction,
            tranche_2_fraction=tp_cfg.tranche_2_sell_fraction,
            tranche_3_fraction=tp_cfg.tranche_3_sell_fraction,
        )
        logger.info(
            "TAKE PROFIT tranche %s for %s: selling %d shares (gain %.2f%%)",
            tranche,
            symbol,
            qty,
            gain_pct,
        )
        _execute_exit(position, tranche, qty, broker, portfolio)


def _execute_exit(
    position: Position,
    reason: str,
    qty: int,
    broker: SchwabAdapter,
    portfolio: PortfolioManager,
) -> None:
    """Place a sell order and update portfolio state."""
    if qty <= 0:
        logger.warning("Exit requested for %s with qty=0; skipping", position.symbol)
        return

    try:
        broker.place_market_order(position.symbol, qty, "SELL")
    except (SchwabAPIError, SchwabAuthError) as exc:
        logger.error("Sell order failed for %s: %s", position.symbol, exc)
        return

    position.current_shares -= qty
    if reason not in position.tranches_sold:
        position.tranches_sold.append(reason)

    if position.current_shares <= 0:
        portfolio.remove_position(position.symbol)
        logger.info("Position %s fully closed (reason: %s)", position.symbol, reason)
    else:
        portfolio.update_position(position)
        logger.info(
            "Position %s partially closed (reason: %s); %d shares remain",
            position.symbol,
            reason,
            position.current_shares,
        )


def _enter_position(
    stock: dict,
    cfg: Config,
    portfolio: PortfolioManager,
    broker: SchwabAdapter,
) -> None:
    """Size and execute an entry order for *stock*."""
    symbol = stock.get("symbol", "")

    # Refresh price to compute actual share count
    try:
        price = broker.get_last_price(symbol)
    except SchwabAPIError as exc:
        logger.error("Could not fetch price for %s: %s", symbol, exc)
        # Fall back to scanner price
        price = float(stock.get("price", 0))
    if price <= 0:
        logger.error("Invalid price %.2f for %s; skipping", price, symbol)
        return

    qty = compute_shares(price, cfg.trading.block_size)
    if qty <= 0:
        logger.warning(
            "Cannot buy %s: price $%.2f exceeds block size $%.0f",
            symbol,
            price,
            cfg.trading.block_size,
        )
        return

    logger.info(
        "ENTRY signal for %s: price=%.2f qty=%d (block=$%.0f) rsi=%.1f signal_type=%s",
        symbol,
        price,
        qty,
        cfg.trading.block_size,
        float(stock.get("rsi") or 0),
        stock.get("signal_type"),
    )

    try:
        broker.place_market_order(symbol, qty, "BUY")
    except (SchwabAPIError, SchwabAuthError) as exc:
        logger.error("Buy order failed for %s: %s", symbol, exc)
        return

    now = datetime.now(timezone.utc)
    position = Position(
        symbol=symbol,
        entry_price=price,
        entry_time=now,
        original_shares=qty,
        current_shares=qty,
    )
    try:
        portfolio.add_position(position)
    except ValueError as exc:
        logger.error("Could not record position for %s: %s", symbol, exc)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _auth_flow(cfg: Config) -> None:
    """Interactive first-time authorisation flow."""
    broker = SchwabAdapter(
        app_key=cfg.schwab.app_key,
        app_secret=cfg.schwab.app_secret,
        redirect_uri=cfg.schwab.redirect_uri,
        account_hash=cfg.schwab.account_hash,
        tokens_file=cfg.schwab.tokens_file,
        dry_run=False,
    )
    url = broker.get_authorization_url()
    print("\n" + "=" * 60)
    print("Schwab OAuth 2.0 Authorisation")
    print("=" * 60)
    print(f"\n1. Open this URL in your browser:\n\n   {url}\n")
    print("2. Log in with your Schwab credentials and grant access.")
    print("3. After redirecting, copy the FULL redirect URL from your")
    print("   browser's address bar and paste it below.\n")
    redirect_url = input("Redirect URL: ").strip()
    if not redirect_url:
        print("No URL entered; aborting.")
        sys.exit(1)
    broker.exchange_code_for_tokens(redirect_url)
    print(f"\n✓ Tokens saved to {cfg.schwab.tokens_file}")
    print("You can now run the bot normally.\n")


def _list_accounts(cfg: Config) -> None:
    """Print all account numbers and their hash values."""
    broker = SchwabAdapter(
        app_key=cfg.schwab.app_key,
        app_secret=cfg.schwab.app_secret,
        redirect_uri=cfg.schwab.redirect_uri,
        account_hash=cfg.schwab.account_hash,
        tokens_file=cfg.schwab.tokens_file,
        dry_run=False,
    )
    accounts = broker.get_account_numbers()
    print("\nAccounts:")
    for acct in accounts:
        print(f"  accountNumber: {acct.get('accountNumber')}  →  hashValue: {acct.get('hashValue')}")
    print("\nSet SCHWAB_ACCOUNT_HASH=<hashValue> in your .env file.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Schwab Trading Bot — Trade Scanner 2 strategy"
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        default=None,
        help="Path to config.yaml (default: use env vars only)",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Run the first-time OAuth 2.0 authorisation flow",
    )
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="List Schwab account numbers and their hash values",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit (useful for cron/testing)",
    )
    args = parser.parse_args(argv)

    cfg = load_config(config_file=args.config)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, cfg.bot.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.auth:
        _auth_flow(cfg)
        return

    if args.list_accounts:
        _list_accounts(cfg)
        return

    # Validate required settings
    if not cfg.schwab.app_key or not cfg.schwab.app_secret:
        logger.error(
            "SCHWAB_APP_KEY and SCHWAB_APP_SECRET must be set. "
            "Copy .env.example to .env and fill in your credentials."
        )
        sys.exit(1)
    if not cfg.schwab.account_hash and not cfg.bot.dry_run:
        logger.error(
            "SCHWAB_ACCOUNT_HASH must be set for live trading. "
            "Run --list-accounts to find it."
        )
        sys.exit(1)

    mode = "DRY-RUN" if cfg.bot.dry_run else "LIVE"
    logger.info(
        "Starting Schwab Trading Bot [%s] | max_positions=%d | block=$%.0f | "
        "stop_loss=%.1f%% | max_hold=%d days",
        mode,
        cfg.trading.max_positions,
        cfg.trading.block_size,
        cfg.trading.stop_loss_pct,
        cfg.trading.max_hold_days,
    )

    broker = SchwabAdapter(
        app_key=cfg.schwab.app_key,
        app_secret=cfg.schwab.app_secret,
        redirect_uri=cfg.schwab.redirect_uri,
        account_hash=cfg.schwab.account_hash,
        tokens_file=cfg.schwab.tokens_file,
        dry_run=cfg.bot.dry_run,
    )
    portfolio = PortfolioManager(
        state_file=cfg.bot.state_file,
        max_positions=cfg.trading.max_positions,
    )

    if args.once:
        run_cycle(cfg, portfolio, broker)
        return

    # Graceful shutdown on SIGINT / SIGTERM
    _running = True

    def _stop(signum: int, frame: object) -> None:
        nonlocal _running
        logger.info("Shutdown signal received; stopping after current cycle …")
        _running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while _running:
        try:
            run_cycle(cfg, portfolio, broker)
        except Exception as exc:
            logger.exception("Unexpected error in bot cycle: %s", exc)

        if not _running:
            break
        logger.info("Sleeping %d seconds …", cfg.bot.poll_interval_seconds)
        # Sleep in 1-second increments so we respond quickly to shutdown signals
        for _ in range(cfg.bot.poll_interval_seconds):
            if not _running:
                break
            time.sleep(1)

    logger.info("Bot stopped.")


if __name__ == "__main__":
    main()
