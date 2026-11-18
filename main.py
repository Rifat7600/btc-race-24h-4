"""EMA trend bot for make.fun btc-race-24h-20261118-1444.

Speaks agent-fun/bars-replay/v1 on stdin/stdout: hello once, then one
batch per minute, then done. Long only, no shorts, no leverage. Strategy:
fast/slow EMA crossover with an ATR stop and trailing exit, sized as a
fixed fraction of equity. Fully deterministic: no clock, no randomness,
no environment reads; every decision follows from the bars seen so far.
"""

import json
import sys

PARAMS = {
    "fast": 30,
    "slow": 120,
    "entry_bps": 3.0,
    "ext_bps": 60.0,
    "stop_mult": 8.0,
    "trail_mult": 100.0,
    "target_bps": 80.0,
    "size_frac": 1.0,
    "cooldown": 30,
    "slope_bars": 60,
    "slope_bps": -10000.0,
    "pullback_bars": 100000,
    "lookback": 480,
    "discount_bps": 120.0,
    "atr_len": 14,
    "max_slip_bps": 100,
    "cross_fresh": 6,
    "exit_mode": 1,
}


class SymState:
    def __init__(self):
        self.n = 0
        self.ema_fast = None
        self.ema_slow = None
        self.prev_fast = None
        self.prev_slow = None
        self.bars_since_cross = 10 ** 9
        self.slow_hist = []
        self.trs = []
        self.atr = None
        self.prev_close = None
        self.entry_px = None
        self.peak = None
        self.bars_since_pullback = 10 ** 9
        self.closes = []
        self.last_exit_bar = -10 ** 9
        self.order_n = 0

    def update(self, o, h, l, c):
        p = PARAMS
        self.n += 1
        if self.ema_fast is None:
            self.ema_fast = c
            self.ema_slow = c
        else:
            self.prev_fast = self.ema_fast
            self.prev_slow = self.ema_slow
            af = 2.0 / (p["fast"] + 1)
            aslow = 2.0 / (p["slow"] + 1)
            self.ema_fast += af * (c - self.ema_fast)
            self.ema_slow += aslow * (c - self.ema_slow)
            if self.prev_fast is not None and self.prev_slow is not None:
                if self.prev_fast <= self.prev_slow and self.ema_fast > self.ema_slow:
                    self.bars_since_cross = 0
                else:
                    self.bars_since_cross += 1
        self.slow_hist.append(self.ema_slow)
        self.closes.append(c)
        if len(self.closes) > p["lookback"]:
            self.closes.pop(0)
        if c < self.ema_fast:
            self.bars_since_pullback = 0
        else:
            self.bars_since_pullback += 1
        if self.prev_close is not None:
            tr = max(h - l, abs(h - self.prev_close), abs(l - self.prev_close))
            self.trs.append(tr)
            if len(self.trs) >= p["atr_len"]:
                self.atr = sum(self.trs[-p["atr_len"]:]) / p["atr_len"]
        self.prev_close = c


class Bot:
    def __init__(self, hello):
        self.symbols = {}
        self.scales = {}
        for s in hello["symbols"]:
            self.symbols[s["symbol"]] = SymState()
            self.scales[s["symbol"]] = (s["price_scale"], s["qty_scale"])
        self.quote_scale = hello["capital"]["quote_scale"]
        self.cash = int(hello["capital"]["quote_units"])
        self.equity = self.cash
        self.fee_bps = hello["fees"]["fee_bps"]
        self.slip_bps = hello["fees"]["slippage_bps"]
        self.min_notional = int(hello["fees"]["min_notional_quote_units"])
        self.max_weight_bps = hello["caps"]["max_weight_bps"]
        self.max_orders = hello["caps"]["max_orders_per_bar"]
        self.warmup = hello.get("warmup_bars", 0)
        self.positions = {}

    def _entry_price(self, sym):
        pos = self.positions.get(sym)
        if not pos or pos[0] <= 0:
            return None
        qty, cost = pos
        ps, qs = self.scales[sym]
        return cost * (10.0 ** qs) / (qty * (10.0 ** self.quote_scale))

    def on_batch(self, batch):
        seq = batch["seq"]
        acct = batch["account"]
        self.cash = int(acct["cash_quote_units"])
        self.equity = int(acct["equity_quote_units"])
        self.positions = {}
        for pos in acct["positions"]:
            self.positions[pos["symbol"]] = (int(pos["qty"]), int(pos["cost_quote_units"]))
        p = PARAMS
        orders = []
        for bar in batch["bars"]:
            sym = bar["symbol"]
            st = self.symbols[sym]
            ps, qs = self.scales[sym]
            scale = 10.0 ** ps
            o = int(bar["o"]) / scale
            h = int(bar["h"]) / scale
            l = int(bar["l"]) / scale
            c = int(bar["c"]) / scale
            st.update(o, h, l, c)
            qty = self.positions.get(sym, (0, 0))[0]
            if batch["seq"] < self.warmup:
                continue
            if qty == 0:
                if self._want_in(sym, st, c):
                    notional = int(p["size_frac"] * self.equity)
                    if notional > self.cash:
                        notional = self.cash
                    if notional >= self.min_notional and len(orders) < self.max_orders:
                        st.order_n += 1
                        orders.append({
                            "side": "buy",
                            "client_id": "b%d" % st.order_n,
                            "symbol": sym,
                            "notional_quote_units": str(notional),
                            "max_slippage_bps": p["max_slip_bps"],
                        })
            else:
                if self._want_out(sym, st, c):
                    st.order_n += 1
                    orders.append({
                        "side": "sell",
                        "client_id": "s%d" % st.order_n,
                        "symbol": sym,
                        "qty": str(qty),
                        "max_slippage_bps": p["max_slip_bps"],
                    })
                    st.last_exit_bar = st.n
                    st.entry_px = None
                    st.peak = None
            if qty > 0:
                ep = self._entry_price(sym)
                if ep is not None:
                    st.entry_px = ep
                    if st.peak is None or c > st.peak:
                        st.peak = c
        return {"type": "reply", "seq": seq, "orders": orders}

    def _want_in(self, sym, st, c):
        p = PARAMS
        if st.n < p["slow"]:
            return False
        if st.n - st.last_exit_bar < p["cooldown"]:
            return False
        if st.atr is None:
            return False
        if any(self.positions.get(s, (0, 0))[0] > 0 for s in self.symbols):
            return False
        if not (st.ema_fast > st.ema_slow):
            return False
        if c <= st.ema_fast:
            return False
        if st.bars_since_pullback > p["pullback_bars"]:
            return False
        if c >= st.ema_fast * (1.0 + p["ext_bps"] / 10000.0):
            return False
        back = st.slow_hist[-p["slope_bars"] - 1] if len(st.slow_hist) > p["slope_bars"] else st.slow_hist[0]
        if not (st.ema_slow > back * (1.0 + p["slope_bps"] / 10000.0)):
            return False
        hi = max(st.closes) if st.closes else c
        if (hi / c - 1.0) * 10000.0 < p["discount_bps"]:
            return False
        return True

    def _want_out(self, sym, st, c):
        p = PARAMS
        if st.ema_fast < st.ema_slow:
            return True
        if st.atr is None:
            return False
        if p["target_bps"] > 0 and st.entry_px is not None and c >= st.entry_px * (1.0 + p["target_bps"] / 10000.0):
            return True
        if st.entry_px is not None and c <= st.entry_px - p["stop_mult"] * st.atr:
            return True
        if st.peak is not None and c <= st.peak - p["trail_mult"] * st.atr:
            return True
        return False


def main():
    bot = None
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "hello":
            bot = Bot(msg)
        elif kind == "batch":
            if bot is None:
                continue
            reply = bot.on_batch(msg)
            out.write(json.dumps(reply, separators=(",", ":")) + "\n")
            out.flush()
        elif kind == "done":
            break


if __name__ == "__main__":
    main()
