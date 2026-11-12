#!/usr/bin/env python3
"""Deterministic EMA trend-following bot for the make.fun btc-race-24h bounty.

Reads one JSON bar (1-minute OHLCV) per line on stdin and writes exactly one
JSON order line per bar on stdout. Standard library only; no network access,
no wall clock, no filesystem reads, no randomness -- the same input stream
always produces byte-identical output.

Strategy: long-only EMA(20)/EMA(60) crossover with a Wilder ATR(14) trailing
stop and a 2% hard stop. See README.md for the full contract.
"""

import json
import sys

FAST_PERIOD = 20
SLOW_PERIOD = 60
ATR_PERIOD = 14
WARMUP_BARS = SLOW_PERIOD + ATR_PERIOD  # 74 bars before any trade is allowed
ENTRY_FRACTION = 0.35  # fraction of equity deployed on each entry
TRAIL_MULTIPLE = 2.0   # trailing stop distance, in ATR multiples
HARD_STOP_PCT = 0.02   # 2% hard stop below the entry price
START_EQUITY = 1000.0


class Ema:
    """Exponential moving average seeded with the SMA of the first `period`
    values. Updated incrementally, one value at a time."""

    def __init__(self, period):
        self.period = period
        self.alpha = 2.0 / (period + 1.0)
        self.count = 0
        self._seed_sum = 0.0
        self.value = 0.0

    def update(self, x):
        self.count += 1
        if self.count <= self.period:
            # Still seeding: average of everything seen so far. Once the
            # window is full this is exactly the SMA of the first `period`
            # values, which then anchors all later EMA updates.
            self._seed_sum += x
            self.value = self._seed_sum / self.count
        else:
            self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return self.value


class WilderAtr:
    """Wilder ATR: true range averaged over the first `period` ranges, then
    smoothed as a = (a * (period - 1) + tr) / period."""

    def __init__(self, period):
        self.period = period
        self.count = 0
        self._seed_sum = 0.0
        self.value = 0.0
        self.prev_close = None

    def update(self, high, low, close):
        if self.prev_close is None:
            # First bar has no previous close, hence no true range yet.
            self.prev_close = close
            return self.value
        tr = max(high - low,
                 abs(high - self.prev_close),
                 abs(low - self.prev_close))
        self.prev_close = close
        self.count += 1
        if self.count <= self.period:
            self._seed_sum += tr
            self.value = self._seed_sum / self.count
        else:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value


class Bot:
    """Holds the indicator state, the simulated account, and the open
    position. One instance processes the whole bar stream, in order."""

    def __init__(self, starting_cash=START_EQUITY):
        self.cash = starting_cash
        self.position = 0.0
        self.entry_price = None
        self.peak_close = None  # highest close seen since entry (trailing)
        self.fast = Ema(FAST_PERIOD)
        self.slow = Ema(SLOW_PERIOD)
        self.atr = WilderAtr(ATR_PERIOD)
        self.bars_seen = 0
        self.prev_fast = None
        self.prev_slow = None

    def equity(self, close):
        return self.cash + self.position * close

    def on_bar(self, bar):
        close = float(bar["c"])
        high = float(bar["h"])
        low = float(bar["l"])

        self.bars_seen += 1
        fast = self.fast.update(close)
        slow = self.slow.update(close)
        atr = self.atr.update(high, low, close)

        crossed_up = (self.prev_fast is not None
                      and self.prev_fast <= self.prev_slow
                      and fast > slow)
        crossed_down = (self.prev_fast is not None
                        and self.prev_fast >= self.prev_slow
                        and fast < slow)
        self.prev_fast = fast
        self.prev_slow = slow

        orders = []

        # Manage the open position first: stops are evaluated every bar.
        if self.position > 0:
            if close > self.peak_close:
                self.peak_close = close
            trailing = self.peak_close - TRAIL_MULTIPLE * atr
            hard = self.entry_price * (1.0 - HARD_STOP_PCT)
            if close <= max(trailing, hard) or crossed_down:
                orders.append({"side": "sell", "qty": round(self.position, 10)})
                self.cash += self.position * close
                self.position = 0.0
                self.entry_price = None
                self.peak_close = None

        # New entry on a bullish EMA cross, once warmed up.
        if (self.position == 0 and crossed_up
                and self.bars_seen >= WARMUP_BARS):
            qty = ENTRY_FRACTION * self.equity(close) / close
            if qty > 0:
                orders.append({"side": "buy", "qty": round(qty, 10)})
                self.cash -= qty * close
                self.position = qty
                self.entry_price = close
                self.peak_close = close

        return orders


def main():
    bot = Bot()
    write = sys.stdout.write
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            bar = json.loads(line)
            orders = bot.on_bar(bar)
        except (ValueError, KeyError, TypeError):
            # Malformed bar (bad JSON, missing field, non-numeric value):
            # hold, leave the bot state untouched, and still emit exactly
            # one output line so the 1:1 input/output contract holds.
            # Safe: on_bar extracts and converts all fields before it
            # mutates any state, so a failure here changes nothing.
            orders = []
        write(json.dumps({"orders": orders}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
