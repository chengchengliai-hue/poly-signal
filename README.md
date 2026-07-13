# Poly Signal v2

Polymarket smart-money monitoring and virtual copy-trading system. The service polls public trade activity, detects new wallets and small-test/heavy-bet patterns, confirms candidate signals after a delay, sends Telegram alerts, and records simulated positions and settlements in SQLite.

## Signal Flow

1. Poll recent Polymarket trades every 10 seconds.
2. Select BUY trades worth at least `$2000`.
3. Detect either a new wallet or a small-test/heavy-bet wallet.
4. Wait at least 45 seconds and reject rapid round trips.
5. Score the signal and send a Telegram alert.
6. Simulate entry at the CLOB best ask and exit at the best bid.
7. Track positions, exits, settlement, PnL, and related-market metadata.

The small-test/heavy-bet rule currently requires 3-50 historical BUY orders, a current order strictly above `$2000`, and a current amount at least 10 times the historical median.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Virtual copy trading is enabled by default. Keep `.env` and `data/` out of source control.

## Tests

```bash
PYTHONPATH=. pytest -q
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the complete data flow, scoring rules, database model, Telegram commands, and deployment layout.
