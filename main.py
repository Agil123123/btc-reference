#!/usr/bin/env python3
"""cbBTC bars-replay/v1 bot: EMA(20)/EMA(60) trend-follow with ATR(14) trailing stop.

Speaks agent-fun/bars-replay/v1 over stdin/stdout, one JSON line at a time:
hello (capture symbols/capital/caps/fees/warmup), then exactly one reply per
batch echoing its seq, then done (flush, exit 0, no reply). Standard library
only, deterministic: no clock, no environment, no randomness, no filesystem
access, no dict-order dependence in the output.
"""
import json
import sys

PROTOCOL = "agent-fun/bars-replay/v1"
FAST = 20
SLOW = 60
ATR_N = 14
WARM_BARS = SLOW + ATR_N
SIZE_NUM = 35
SIZE_DEN = 100
TRAIL_ATR = 2.0
HARD_STOP = 0.02
MAX_SLIPPAGE_BPS = 100
BPS = 10000


def _as_int(value, default):
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v > 1e308 or v < -1e308:
        return None
    return v


class Ema:
    """EMA seeded with the SMA of its first `period` closes; None until seeded."""

    def __init__(self, period):
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self._warm = []
        self.value = None

    def update(self, x):
        if self.value is not None:
            self.value += self.alpha * (x - self.value)
        else:
            self._warm.append(x)
            if len(self._warm) >= self.period:
                self.value = sum(self._warm) / len(self._warm)
                self._warm = []
        return self.value


class WilderAtr:
    """Wilder ATR seeded with the mean of its first `period` true ranges."""

    def __init__(self, period):
        self.period = period
        self._trs = []
        self.value = None
        self._prev_c = None

    def update(self, h, l, c):
        if self._prev_c is None:
            self._prev_c = c
            return None
        tr = max(h - l, abs(h - self._prev_c), abs(l - self._prev_c))
        self._prev_c = c
        if self.value is not None:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        else:
            self._trs.append(tr)
            if len(self._trs) >= self.period:
                self.value = sum(self._trs) / len(self._trs)
                self._trs = []
        return self.value


class Bot:
    def __init__(self):
        self.symbol = "CBBTC"
        self.price_scale = 5
        self.qty_scale = 8
        self.quote_scale = 6
        self.warmup_bars = 0
        self.max_orders = 1
        self.max_weight_bps = BPS
        self.min_notional = 0
        self.fast = Ema(FAST)
        self.slow = Ema(SLOW)
        self.atr = WilderAtr(ATR_N)
        self.n_bars = 0
        self.gap_prev = None
        self.entry_close = None
        self.peak = None
        self._next_id = 1

    def on_hello(self, msg):
        try:
            symbols = msg.get("symbols")
            if isinstance(symbols, list) and symbols:
                s0 = symbols[0]
                if isinstance(s0, dict):
                    name = s0.get("symbol")
                    if isinstance(name, str) and name:
                        self.symbol = name
                    self.price_scale = max(0, _as_int(s0.get("price_scale"), self.price_scale))
                    self.qty_scale = max(0, _as_int(s0.get("qty_scale"), self.qty_scale))
            capital = msg.get("capital")
            if isinstance(capital, dict):
                self.quote_scale = max(0, _as_int(capital.get("quote_scale"), self.quote_scale))
            caps = msg.get("caps")
            if isinstance(caps, dict):
                self.max_orders = max(0, _as_int(caps.get("max_orders_per_bar"), self.max_orders))
                self.max_weight_bps = max(0, _as_int(caps.get("max_weight_bps"), self.max_weight_bps))
            fees = msg.get("fees")
            if isinstance(fees, dict):
                self.min_notional = max(0, _as_int(fees.get("min_notional_quote_units"), self.min_notional))
            self.warmup_bars = max(0, _as_int(msg.get("warmup_bars"), self.warmup_bars))
        except Exception:
            pass

    def on_batch(self, batch):
        seq = _as_int(batch.get("seq"), 0)
        try:
            orders = self._decide(batch, seq)
        except Exception:
            orders = []
        if len(orders) > self.max_orders:
            orders = orders[: self.max_orders]
        return {"type": "reply", "seq": seq, "orders": orders}

    def _decide(self, batch, seq):
        account = batch.get("account")
        if not isinstance(account, dict):
            account = {}
        equity = max(0, _as_int(account.get("equity_quote_units"), 0))
        positions = account.get("positions")
        if not isinstance(positions, list):
            positions = []
        held_qty = None
        for p in positions:
            if not isinstance(p, dict) or p.get("symbol") != self.symbol:
                continue
            raw = p.get("qty")
            if isinstance(raw, bool) or not isinstance(raw, (int, str)):
                continue
            try:
                q = int(raw)
            except (TypeError, ValueError):
                continue
            if q > 0:
                held_qty = raw if isinstance(raw, str) else str(raw)
                break
        bars = batch.get("bars")
        if not isinstance(bars, list):
            bars = []
        saw_bar = False
        last_close = None
        batch_high = None
        cross_up = False
        cross_dn = False
        for bar in bars:
            if not isinstance(bar, dict) or bar.get("symbol") != self.symbol:
                continue
            parsed = self._parse_bar(bar)
            if parsed is None:
                continue
            c, h, l = parsed
            saw_bar = True
            self.n_bars += 1
            fast = self.fast.update(c)
            slow = self.slow.update(c)
            self.atr.update(h, l, c)
            gap = None
            if fast is not None and slow is not None:
                gap = fast - slow
            if gap is not None and self.gap_prev is not None:
                cross_up = gap > 0 and self.gap_prev <= 0
                cross_dn = gap < 0 and self.gap_prev >= 0
            else:
                cross_up = False
                cross_dn = False
            if gap is not None:
                self.gap_prev = gap
            last_close = c
            batch_high = c if batch_high is None else max(batch_high, c)
        if not saw_bar:
            return []
        gated = seq < self.warmup_bars or self.n_bars < WARM_BARS
        if held_qty is not None:
            if self.entry_close is None:
                self.entry_close = last_close
                self.peak = batch_high
                return []
            self.peak = max(self.peak, batch_high)
            if gated:
                return []
            stop = self.entry_close * (1.0 - HARD_STOP)
            atr_now = self.atr.value
            if atr_now is not None:
                stop = max(stop, self.peak - TRAIL_ATR * atr_now)
            if cross_dn or last_close <= stop:
                self.entry_close = None
                self.peak = None
                return [self._sell(held_qty)]
            return []
        if gated or not cross_up:
            return []
        # Flat: current_value = qty*close*10^quote_scale // 10^(qty_scale+price_scale) = 0.
        current_value = 0
        room = self.max_weight_bps * equity // BPS - current_value
        notional = equity * SIZE_NUM // SIZE_DEN
        if notional > room:
            notional = room
        if notional <= 0 or notional < self.min_notional:
            return []
        self.entry_close = last_close
        self.peak = last_close
        return [self._buy(notional)]

    @staticmethod
    def _parse_bar(bar):
        c = _as_float(bar.get("c"))
        if c is None:
            return None
        h = _as_float(bar.get("h"))
        if h is None:
            h = c
        l = _as_float(bar.get("l"))
        if l is None:
            l = c
        return (c, h, l)

    def _client_id(self):
        cid = "o%d" % self._next_id
        self._next_id += 1
        return cid

    def _buy(self, notional):
        return {
            "side": "buy",
            "client_id": self._client_id(),
            "symbol": self.symbol,
            "notional_quote_units": str(notional),
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        }

    def _sell(self, qty):
        return {
            "side": "sell",
            "client_id": self._client_id(),
            "symbol": self.symbol,
            "qty": qty,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        }


def main():
    bot = Bot()
    out = sys.stdout
    src = getattr(sys.stdin, "buffer", sys.stdin)
    for raw in src:
        if isinstance(raw, bytes):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            line = raw
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        mtype = msg.get("type")
        if mtype == "hello":
            try:
                bot.on_hello(msg)
            except Exception:
                pass
        elif mtype == "batch":
            reply = bot.on_batch(msg)
            try:
                out.write(json.dumps(reply, separators=(",", ":"), ensure_ascii=False) + "\n")
                out.flush()
            except OSError:
                return
        elif mtype == "done":
            try:
                out.flush()
            except OSError:
                pass
            return
        # unknown message types: skip silently, no reply


if __name__ == "__main__":
    main()
