"""bt_binary.py — backtest event-driven dengan backtrader.

Kenapa backtrader: ia memproses bar SATU PER SATU. Tidak ada cara membaca
candle masa depan. Jadi hasilnya adalah batas atas yang jujur untuk feed live.

Model binary option:
  - sinyal dihitung di CLOSE candle A (bar yang sudah tutup)
  - entry di OPEN candle B (bar berikutnya)  <- backtrader default
  - menang bila arah candle B (close vs open) sesuai sinyal
  - payout P% bila menang, -100% bila kalah

Aturan yang diuji (dari audit proyek):
  1. z>2 revert          — fade deviasi 2x dari SMA20
  2. z>2 + VOL HI        — aturan final aplikasi (filter rezim volatilitas)
  3. ema9/21 follow      — aturan lama
  4. control always CALL — pembanding netral
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import backtrader as bt
import pandas as pd

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"


class BinaryOptionStrategy(bt.Strategy):
    """Satu 'trade' = satu candle. Menang bila arah candle entry sesuai sinyal."""

    params = (
        ("rule", "z2"),          # z2 | z2vol | ema | always_call
        ("payout", 0.80),        # payout broker
        ("stake", 1.0),          # ukuran per trade (unit)
        ("z_thresh", 2.0),
        ("vol_period", 60),
    )

    def __init__(self):
        self.sma20 = bt.ind.SMA(self.data.close, period=20)
        self.dev = bt.ind.StdDev(self.data.close, period=20)
        self.ema9 = bt.ind.EMA(self.data.close, period=9)
        self.ema21 = bt.ind.EMA(self.data.close, period=21)
        self.rng = self.data.high - self.data.low
        self.med_rng = bt.ind.SMA(self.rng, period=self.p.vol_period)

        self.pending = None      # arah sinyal yang menunggu candle berikutnya
        self.entry_open = None
        self.n = 0
        self.wins = 0
        self.losses = 0
        self.equity = 0.0
        self.curve = []

    def _signal(self):
        """Hitung sinyal dari bar yang SUDAH TUTUP. Tidak ada look-ahead."""
        rule = self.p.rule
        if rule == "always_call":
            return 1
        if rule == "ema":
            if self.ema9[0] > self.ema21[0]:
                return 1
            if self.ema9[0] < self.ema21[0]:
                return -1
            return 0
        # z-score
        if self.dev[0] == 0:
            return 0
        z = (self.data.close[0] - self.sma20[0]) / self.dev[0]
        if abs(z) <= self.p.z_thresh:
            return 0
        if rule == "z2vol":
            if not (self.rng[0] > self.med_rng[0]):
                return 0
        return -1 if z > 0 else 1

    def next(self):
        # 1) selesaikan trade yang entry-nya bar ini
        if self.pending is not None:
            o = self.data.open[0]
            c = self.data.close[0]
            if c > o:
                actual = 1
            elif c < o:
                actual = -1
            else:
                actual = 0
            self.n += 1
            if actual == 0:
                # draw: stake kembali, tidak dihitung win/loss
                self.equity += 0.0
            elif actual == self.pending:
                self.wins += 1
                self.equity += self.p.stake * self.p.payout
            else:
                self.losses += 1
                self.equity -= self.p.stake
            self.curve.append(self.equity)
            self.pending = None

        # 2) sinyal baru dari bar ini (tutup) -> entry bar berikutnya
        if len(self.data) > self.p.vol_period + 25:
            s = self._signal()
            if s != 0:
                self.pending = s


def load_symbol(symbol, limit=None):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = ("select time,open,high,low,close from candles_m1 "
         "where symbol=? order by time")
    rows = con.execute(q, (symbol,)).fetchall()
    con.close()
    if limit:
        rows = rows[:limit]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("datetime")
    df["openinterest"] = 0.0
    return df[["open", "high", "low", "close", "openinterest"]]


def run(symbols, rule, payout, verbose=False):
    tot_n = tot_w = tot_l = 0
    tot_eq = 0.0
    for sym in symbols:
        df = load_symbol(sym)
        if len(df) < 200:
            continue
        cerebro = bt.Cerebro(stdstats=False)
        cerebro.addstrategy(BinaryOptionStrategy, rule=rule, payout=payout)
        cerebro.adddata(bt.feeds.PandasData(dataname=df))
        cerebro.broker.setcash(1_000_000)
        cerebro.broker.setcommission(commission=0.0)
        strat = cerebro.run()[0]
        tot_n += strat.n
        tot_w += strat.wins
        tot_l += strat.losses
        tot_eq += strat.equity
        if verbose:
            wr = strat.wins / strat.n * 100 if strat.n else 0
            print(f"  {sym:16} n={strat.n:5d} win={strat.wins:5d} "
                  f"loss={strat.losses:5d} wr={wr:6.2f}% eq={strat.equity:+9.2f}")
    return tot_n, tot_w, tot_l, tot_eq


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=12)
    ap.add_argument("--payout", type=float, default=0.80)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    syms = [r[0] for r in con.execute(
        "select symbol from candles_m1 group by symbol having count(*) > 2000 "
        "order by symbol limit ?", (args.symbols,))]
    con.close()

    print(f"backtrader event-driven | {len(syms)} simbol | payout {args.payout:.0%}")
    print("sinyal di close candle A -> entry di open candle B (tanpa look-ahead)\n")

    print(f"{'aturan':16} {'n':>7} {'win':>7} {'loss':>7} {'winrate':>9} {'lb95':>7} {'P/L':>10}")
    print("-" * 70)
    for rule in ("z2", "z2vol", "ema", "always_call"):
        n, w, l, eq = run(syms, rule, args.payout, verbose=args.verbose)
        wr = w / n * 100 if n else 0
        print(f"{rule:16} {n:7d} {w:7d} {l:7d} {wr:8.2f}% {wilson_low(w, n):6.2f}% {eq:+10.2f}")

    print("\nCatatan: 'P/L' = akumulasi unit pada stake 1, payout "
          f"{args.payout:.0%}. Negatif = rugi.")
    print("Break-even winrate = 1/(1+payout) = "
          f"{1 / (1 + args.payout) * 100:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
