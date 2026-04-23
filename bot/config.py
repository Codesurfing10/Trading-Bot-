"""
Configuration loader.

Values are resolved in this priority order (highest wins):
  1. Environment variables / .env file
  2. config.yaml (path passed to load_config)
  3. Hard-coded defaults
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    import yaml as _yaml

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ScannerConfig:
    url: str = "https://codesurfing10.github.io/Trade-Scanner-2/stocks.json"
    timeout_seconds: int = 30


@dataclass
class TradingConfig:
    max_positions: int = 2
    block_size: float = 1000.0
    stop_loss_pct: float = 2.5
    max_hold_days: int = 5
    rsi_overbought: float = 70.0


@dataclass
class TakeProfitConfig:
    """Take-profit tranche schedule.

    Tranche schedule (as fractions of *original* shares):
      T1 (+5 %):  sell tranche_1_sell_fraction  (default 50 %)
      T2 (+10 %): sell tranche_2_sell_fraction  (default 25 %)
      T3 (+25 %): sell tranche_3_sell_fraction  (default 25 %)
      Hard exit (+30 %): close any remaining shares
    """

    tranche_1_pct: float = 5.0
    tranche_1_sell_fraction: float = 0.50
    tranche_2_pct: float = 10.0
    tranche_2_sell_fraction: float = 0.25
    tranche_3_pct: float = 25.0
    tranche_3_sell_fraction: float = 0.25
    hard_exit_pct: float = 30.0


@dataclass
class SchwabConfig:
    app_key: str = ""
    app_secret: str = ""
    redirect_uri: str = "https://127.0.0.1"
    account_hash: str = ""
    tokens_file: str = "tokens.json"


@dataclass
class BotConfig:
    dry_run: bool = True
    poll_interval_seconds: int = 300
    state_file: str = "state.json"
    log_level: str = "INFO"


@dataclass
class Config:
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    take_profit: TakeProfitConfig = field(default_factory=TakeProfitConfig)
    schwab: SchwabConfig = field(default_factory=SchwabConfig)
    bot: BotConfig = field(default_factory=BotConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(config_file: Optional[str] = None) -> Config:
    """Return a fully-populated Config, merged from defaults → YAML → env."""
    cfg = Config()

    if config_file:
        if _YAML_AVAILABLE:
            _apply_yaml(cfg, config_file)
        else:
            logger.warning(
                "PyYAML not installed; skipping config file %s", config_file
            )

    _apply_env(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _apply_yaml(cfg: Config, path: str) -> None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = _yaml.safe_load(fh)
        if not isinstance(data, dict):
            return
    except (OSError, _yaml.YAMLError) as exc:
        logger.warning("Could not read config file %s: %s", path, exc)
        return

    s = data.get("scanner", {})
    if "url" in s:
        cfg.scanner.url = str(s["url"])
    if "timeout_seconds" in s:
        cfg.scanner.timeout_seconds = int(s["timeout_seconds"])

    t = data.get("trading", {})
    if "max_positions" in t:
        cfg.trading.max_positions = int(t["max_positions"])
    if "block_size" in t:
        cfg.trading.block_size = float(t["block_size"])
    if "stop_loss_pct" in t:
        cfg.trading.stop_loss_pct = float(t["stop_loss_pct"])
    if "max_hold_days" in t:
        cfg.trading.max_hold_days = int(t["max_hold_days"])
    if "rsi_overbought" in t:
        cfg.trading.rsi_overbought = float(t["rsi_overbought"])

    tp = data.get("take_profit", {})
    if "tranche_1_pct" in tp:
        cfg.take_profit.tranche_1_pct = float(tp["tranche_1_pct"])
    if "tranche_1_sell_fraction" in tp:
        cfg.take_profit.tranche_1_sell_fraction = float(tp["tranche_1_sell_fraction"])
    if "tranche_2_pct" in tp:
        cfg.take_profit.tranche_2_pct = float(tp["tranche_2_pct"])
    if "tranche_2_sell_fraction" in tp:
        cfg.take_profit.tranche_2_sell_fraction = float(tp["tranche_2_sell_fraction"])
    if "tranche_3_pct" in tp:
        cfg.take_profit.tranche_3_pct = float(tp["tranche_3_pct"])
    if "tranche_3_sell_fraction" in tp:
        cfg.take_profit.tranche_3_sell_fraction = float(tp["tranche_3_sell_fraction"])
    if "hard_exit_pct" in tp:
        cfg.take_profit.hard_exit_pct = float(tp["hard_exit_pct"])

    sch = data.get("schwab", {})
    if "redirect_uri" in sch:
        cfg.schwab.redirect_uri = str(sch["redirect_uri"])
    if "tokens_file" in sch:
        cfg.schwab.tokens_file = str(sch["tokens_file"])

    b = data.get("bot", {})
    if "dry_run" in b:
        cfg.bot.dry_run = bool(b["dry_run"])
    if "poll_interval_seconds" in b:
        cfg.bot.poll_interval_seconds = int(b["poll_interval_seconds"])
    if "state_file" in b:
        cfg.bot.state_file = str(b["state_file"])
    if "log_level" in b:
        cfg.bot.log_level = str(b["log_level"])


def _apply_env(cfg: Config) -> None:
    """Override cfg values with environment variables when present."""
    if v := os.getenv("SCANNER_URL"):
        cfg.scanner.url = v
    if v := os.getenv("SCANNER_TIMEOUT"):
        cfg.scanner.timeout_seconds = int(v)

    if v := os.getenv("MAX_POSITIONS"):
        cfg.trading.max_positions = int(v)
    if v := os.getenv("BLOCK_SIZE"):
        cfg.trading.block_size = float(v)
    if v := os.getenv("STOP_LOSS_PCT"):
        cfg.trading.stop_loss_pct = float(v)
    if v := os.getenv("MAX_HOLD_DAYS"):
        cfg.trading.max_hold_days = int(v)
    if v := os.getenv("RSI_OVERBOUGHT"):
        cfg.trading.rsi_overbought = float(v)

    if v := os.getenv("SCHWAB_APP_KEY"):
        cfg.schwab.app_key = v
    if v := os.getenv("SCHWAB_APP_SECRET"):
        cfg.schwab.app_secret = v
    if v := os.getenv("SCHWAB_REDIRECT_URI"):
        cfg.schwab.redirect_uri = v
    if v := os.getenv("SCHWAB_ACCOUNT_HASH"):
        cfg.schwab.account_hash = v
    if v := os.getenv("SCHWAB_TOKENS_FILE"):
        cfg.schwab.tokens_file = v

    if v := os.getenv("DRY_RUN"):
        cfg.bot.dry_run = v.strip().lower() not in ("false", "0", "no")
    if v := os.getenv("POLL_INTERVAL_SECONDS"):
        cfg.bot.poll_interval_seconds = int(v)
    if v := os.getenv("STATE_FILE"):
        cfg.bot.state_file = v
    if v := os.getenv("LOG_LEVEL"):
        cfg.bot.log_level = v
