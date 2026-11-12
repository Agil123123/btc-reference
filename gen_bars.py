#!/usr/bin/env python3
"""Deterministic synthetic cbBTC bar generator for local testing.

Uses a fixed-seed linear congruential generator, so every run writes the
same 1,440 bars. No randomness module, no clock, and no input files --
standard library only.
"""

import json
import math

BAR_COUNT = 1440
START_PRICE = 60000.0
FIRST_TIMESTAMP = 1700000000
BAR_SECONDS = 60

_seed = 12345


def _rnd():
    """One LCG step (glibc constants); returns a float in [0, 1)."""
    global _seed
    _seed = (_seed * 1103515245 + 12345) & 0x7FFFFFFF
    return _seed / 0x7FFFFFFF


def main():
    price = START_PRICE
    bars = []
    for i in range(BAR_COUNT):
        drift = math.sin(i / 240.0 * math.pi) * 0.0006
        shock = (_rnd() - 0.5) * 0.0008
        open_price = price
        close_price = price * (1.0 + drift + shock)
        high_price = max(open_price, close_price) * (1.0 + _rnd() * 0.0004)
        low_price = min(open_price, close_price) * (1.0 - _rnd() * 0.0004)
        volume = 100.0 + _rnd() * 1000.0
        bars.append({
            "t": FIRST_TIMESTAMP + i * BAR_SECONDS,
            "o": round(open_price, 2),
            "h": round(high_price, 2),
            "l": round(low_price, 2),
            "c": round(close_price, 2),
            "v": round(volume, 2),
        })
        price = close_price

    with open("sample_bars.jsonl", "w", encoding="utf-8") as handle:
        for bar in bars:
            handle.write(json.dumps(bar) + "\n")
    print(f"wrote sample_bars.jsonl ({len(bars)} bars)")


if __name__ == "__main__":
    main()
