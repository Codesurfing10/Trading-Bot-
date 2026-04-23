# Schwab Trading Bot

A Python trading bot for Charles Schwab brokerage accounts that reads buy/sell
signals from the [Trade Scanner 2](https://codesurfing10.github.io/Trade-Scanner-2/)
and manages positions automatically.

---

## Strategy overview

| Parameter | Value |
|---|---|
| Entry signal | `action_signal == BUY`, Stage 2 advancing, strong volume confirmation |
| Overbought filter | Skip if `signal_type == overbought` **or** RSI ≥ 70 |
| Position size | ≈ $1 000 notional (floor shares) |
| Max concurrent positions | 2 |
| Stop loss | −2.5 % from entry → market sell |
| Take-profit tranche 1 | +5 % → sell **50 %** of original shares |
| Take-profit tranche 2 | +10 % → sell **25 %** of original shares |
| Take-profit tranche 3 | +25 % → sell **25 %** of original shares |
| Hard take-profit | +30 % → sell all remaining shares |
| Time stop | 5 trading days (Mon–Fri) → sell all remaining shares |

---

## Project structure

```
├── bot/
│   ├── __init__.py
│   ├── config.py            # Config loader (.env + config.yaml)
│   ├── scanner_client.py    # Fetches stocks.json from Trade Scanner 2
│   ├── signal_evaluator.py  # Maps scanner data → buy candidates
│   ├── risk_manager.py      # Position sizing, stop-loss, take-profit logic
│   ├── portfolio_manager.py # Persists open positions to state.json
│   ├── schwab_adapter.py    # Schwab OAuth2 + REST API client
│   └── main.py              # Main loop / CLI entry point
├── tests/
│   ├── test_scanner_client.py
│   ├── test_signal_evaluator.py
│   ├── test_portfolio_manager.py
│   └── test_risk_manager.py
├── .env.example             # Environment variable template
├── config.yaml.example      # YAML config template
└── requirements.txt
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Register a Schwab Developer app

1. Visit <https://developer.schwab.com> and create a free account.
2. Create a new app and note the **App Key** and **App Secret**.
3. Set the **Callback URL** (redirect URI) to `https://127.0.0.1`.
4. Enable the **Trader API** product on your app.

### 3. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```dotenv
SCHWAB_APP_KEY=your_app_key_here
SCHWAB_APP_SECRET=your_app_secret_here
SCHWAB_REDIRECT_URI=https://127.0.0.1
```

You can also copy and edit `config.yaml.example → config.yaml` for YAML-based
configuration (useful for non-secret settings).

### 4. Authenticate (first run only)

```bash
python -m bot.main --auth
```

This prints an authorisation URL.  Open it in your browser, log in with your
Schwab credentials, grant access, then paste the redirect URL back into the
terminal.  Tokens are saved to `tokens.json`.

> **Security note:** `tokens.json` contains sensitive OAuth tokens.
> It is listed in `.gitignore` and should never be committed.

### 5. Find your account hash

```bash
python -m bot.main --list-accounts
```

Set `SCHWAB_ACCOUNT_HASH=<hashValue>` in your `.env` file.

---

## Running in paper / dry-run mode

By default `DRY_RUN=true`, which logs all intended orders without placing real
trades.  Verify the logs look correct before switching to live mode.

```bash
python -m bot.main                    # run continuously (dry-run)
python -m bot.main --once             # single cycle then exit
python -m bot.main --config config.yaml  # use YAML config
```

To trade live, set `DRY_RUN=false` in `.env` (or `bot.dry_run: false` in YAML):

```bash
DRY_RUN=false python -m bot.main
```

---

## How scanner data is used

The bot fetches
`https://codesurfing10.github.io/Trade-Scanner-2/stocks.json` on every cycle.

Each record contains:

| Field | Used for |
|---|---|
| `action_signal` | Must be `BUY` to consider entry |
| `signal_type` | `overbought` → skip |
| `rsi` | ≥ 70 → skip (overbought filter) |
| `confirms_stage2_volume` | Must be `Strong Volume Confirmation` |
| `price` | Fallback if live quote unavailable |

Buy candidates are sorted: **oversold first**, then **bullish**, then
**neutral** (lower RSI preferred within each tier).

---

## Risk controls

| Control | Behaviour |
|---|---|
| Max 2 positions | Bot never holds more than 2 concurrent positions |
| Overbought filter | Stocks with RSI ≥ 70 or `signal_type=overbought` are never bought |
| Stop loss | Market sell at −2.5 % from entry price |
| Time stop | Market sell after 5 trading days (Mon–Fri weekdays) |
| Take-profit ladder | See strategy table above |
| Block size | ~$1 000 per trade; share count = `floor(1000 / price)` |
| Dry-run mode | No real orders; all decisions logged |

---

## State persistence

Open positions (entry price, entry time, shares, tranches sold) are persisted
to `state.json` (path configurable via `STATE_FILE`).  The bot resumes
correctly after a restart.

`state.json` is listed in `.gitignore` and should not be committed.

---

## Environment variables reference

| Variable | Default | Description |
|---|---|---|
| `SCHWAB_APP_KEY` | *(required)* | Schwab developer app key |
| `SCHWAB_APP_SECRET` | *(required)* | Schwab developer app secret |
| `SCHWAB_REDIRECT_URI` | `https://127.0.0.1` | OAuth2 redirect URI |
| `SCHWAB_ACCOUNT_HASH` | *(required for live)* | Encrypted account hash |
| `SCHWAB_TOKENS_FILE` | `tokens.json` | OAuth token storage path |
| `DRY_RUN` | `true` | `false` to place real orders |
| `POLL_INTERVAL_SECONDS` | `300` | Seconds between cycles |
| `STATE_FILE` | `state.json` | Position state file |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `SCANNER_URL` | *(Trade Scanner 2 URL)* | Override scanner data URL |
| `MAX_POSITIONS` | `2` | Max concurrent positions |
| `BLOCK_SIZE` | `1000.0` | USD notional per trade |
| `STOP_LOSS_PCT` | `2.5` | Stop-loss percentage |
| `MAX_HOLD_DAYS` | `5` | Max hold in trading days |
| `RSI_OVERBOUGHT` | `70.0` | RSI threshold for overbought filter |

---

## Running tests

```bash
python -m pytest tests/ -v
```

---

## Disclaimer

This software is for educational and research purposes only.  It is not
financial advice.  Trading stocks involves substantial risk of loss.  Always
test thoroughly in dry-run mode before enabling live trading, and never risk
money you cannot afford to lose.