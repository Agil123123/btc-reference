#!/usr/bin/env python3
"""Local replay simulator for the btc-race bot (not run by the grader).

Reads a JSONL bar file, feeds the bars through the bot, and fills each order
at the NEXT bar's open, charging 6 bps of notional (4 bps fee + 2 bps
slippage). Prints final equity, net PnL, max drawdown, and trade count as
JSON. Standard library only; deterministic.

Usage: python3 backtest.py sample_bars.jsonl
"""

import json
import sys

from main import Bot

START_EQUITY = 1000.0
ROUND_TRIP_COST = 0.0006  # 6 bps of notional charged on every fill


def run(path):
    with open(path, "r", encoding="utf-8") as handle:
        bars = [json.loads(line) for line in handle if line.strip()]

    bot = Bot()
    cash = START_EQUITY
    position = 0.0
    peak_equity = START_EQUITY
    worst_drawdown = 0.0
    fills = 0
    pending = []  # orders emitted last bar; they fill at this bar's open

    for bar in bars:
        open_price = float(bar["o"])
        close_price = float(bar["c"])

        for order in pending:
            notional = order["qty"] * open_price
            fee = notional * ROUND_TRIP_COST
            if order["side"] == "buy":
                cash -= notional + fee
                position += order["qty"]
            else:
                cash += notional - fee
                position -= order["qty"]
            fills += 1
        pending = bot.on_bar(bar)

        equity = cash + position * close_price
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity
        worst_drawdown = max(worst_drawdown, drawdown)

    equity = cash + position * float(bars[-1]["c"])
    return {
        "final_equity": round(equity, 2),
        "net_pnl": round(equity - START_EQUITY, 2),
        "max_drawdown_pct": round(worst_drawdown * 100, 3),
        "trades": fills,
    }


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: python3 backtest.py <bars.jsonl>")
    print(json.dumps(run(argv[1]), indent=2))


if __name__ == "__main__":
    main(sys.argv)
