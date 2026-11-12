# BTC Race Bot — EMA Trend-Follow

A deterministic trading bot for the make.fun **btc-race-24h** bounty. It reads
a stream of 1-minute cbBTC/USD bars and emits one order decision per bar. The
race is a pure bar replay: fills happen at the next bar's open, the harness
applies fees, slippage, and pool price impact, and no real money is involved.

## How it works

The bot is long-only and trend-following. It watches two exponential moving
averages of the close price — a fast EMA(20) and a slow EMA(60) — plus a
Wilder ATR(14) for risk control. When the fast average crosses above the slow
one, the bot buys; it holds until the trend reverses or a stop is hit.

## I/O contract

Input: one JSON object per line on stdin, one bar per line:

```json
{"t": 1700000000, "o": 60000.0, "h": 60014.7, "l": 59983.8, "c": 60007.4, "v": 206.7}
```

Output: exactly one JSON object per input line on stdout, in the same order:

```json
{"orders": [{"side": "buy", "qty": 0.0058542594}]}
{"orders": []}
```

An empty `orders` array means hold the current position (or stay flat). `qty`
is denominated in cbBTC; `side` is `"buy"` or `"sell"`.

## Strategy

| Component | Rule |
|-----------|------|
| Warmup | No trades until at least 74 bars (EMA(60) + ATR(14)) have been seen |
| Entry | EMA(20) crosses above EMA(60) |
| Sizing | 35% of current equity, divided by the close price |
| Exit | EMA(20) crosses below EMA(60), or close falls 2×ATR below the peak close since entry, or close falls 2% below the entry price |
| Position | One long position at a time; exits close the full position |

The bot tracks its own cash, position, and equity internally (marking the
position at each bar's close) purely to size entries. The 4 bps fee + 2 bps
slippage + pool impact charged by the harness punish overtrading, so the
strategy deliberately trades rarely and lets winners run behind the trailing
stop.

## Constraints

- Python standard library only — nothing is installed before the bot runs.
- No network access, no wall clock, no filesystem reads inside `main.py`.
- No unseeded randomness; every parameter is a fixed constant.
- Deterministic: the same bar stream always yields byte-identical orders.

## Local verification

```sh
python3 gen_bars.py
python3 main.py < sample_bars.jsonl > o1.jsonl
python3 main.py < sample_bars.jsonl > o2.jsonl
diff o1.jsonl o2.jsonl && echo DETERMINISTIC
python3 backtest.py sample_bars.jsonl
```

Expected: `DETERMINISTIC` is printed, at least one trade is executed, and the
maximum drawdown stays well under 25%.

## Files

| File | Purpose |
|------|---------|
| `main.py` | The entrypoint the grader runs |
| `backtest.py` | Local replay simulator (not run by the grader) |
| `gen_bars.py` | Deterministic synthetic bar generator for testing |
| `sample_bars.jsonl` | Generated sample stream (1,440 bars, not committed) |

## License

MIT — see [LICENSE](LICENSE).
