"""backtest14_lookahead.py — the only way to reach 100%: read the entry candle.

Every honest family of rules (.py 8..13) tops out at 50.26% on this feed. The
user asked for 100% "by any means". There is exactly one means: read the candle
that the trade will be judged on.

Convention (same as backtest5 and the app):
  signal fires at the close of candle A (index i)
  entry is candle B (index i+1)
  the trade is won when B's direction (close vs open) matches the signal

Look-ahead = set the signal to sign(close_B - open_B), which is known in any
historical walk because B already exists in the table. Verifiable result: every
single trade wins.

Also measured, so the gap is explicit:
  * "T-1 leak"  — predict B from A's direction. This is the strongest thing a
    real-time feed can offer, and it is the coin flip that .py 8..13 found.
  * the sample size, so the 100% is not a 3-trade fluke.
"""
import sqlite3
import sys
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    look_w = look_n = 0
    lag_w = lag_n = 0
    per_symbol = []

    for (symbol,) in con.execute("select distinct symbol from candles_m1 order by symbol"):
        rows = con.execute(
            "select open,close from candles_m1 where symbol=? order by time", (symbol,)
        ).fetchall()
        if len(rows) < 60:
            continue
        a = np.array(rows, dtype=float)
        o, c = a[:, 0], a[:, 1]
        # candles: index i is A, i+1 is B; need 25 bars of history for the app's guard
        i = np.arange(25, len(a) - 1)
        b_up = np.sign(c[i + 1] - o[i + 1])          # B's true direction — the secret
        a_up = np.sign(c[i] - o[i])                   # A's direction — the honest proxy
        valid = (b_up != 0)
        # look-ahead always calls B's own direction -> always right
        look_w += int(valid.sum()); look_n += int(valid.sum())
        lag_ok = ((a_up != 0) & valid & ((a_up > 0) == (b_up > 0)))
        lag_w += int(lag_ok.sum()); lag_n += int(((a_up != 0) & valid).sum())
        per_symbol.append((symbol, int(valid.sum())))

    con.close()

    print(f"symbols={len(per_symbol)}  trades={look_n}\n")
    print("LOOK-AHEAD (signal = direction of entry candle B):")
    print(f"  winrate {look_w / look_n * 100:.4f}%   n={look_n}   lb95={wilson_low(look_w, look_n):.4f}")
    print("\nHONEST REAL-TIME (signal = direction of candle A, best a live feed can do):")
    print(f"  winrate {lag_w / lag_n * 100:.4f}%   n={lag_n}   lb95={wilson_low(lag_w, lag_n):.4f}")
    print("\nper-symbol (look-ahead):")
    for s, n in per_symbol[:8]:
        print(f"  {s:16} n={n:6d}  100.00%")
    print("  ...")
    print("\nverdict:")
    print("  100% is arithmetically reachable, and only by reading candle B before it")
    print(f"  exists. It is exactly {look_n} of {look_n} trades. In a live feed the next")
    print("  candle has not been broadcast, so the same rule cannot fire — see the")
    print("  input guard in MainWindow.xaml.cs (returns no signal instead of crashing).")


if __name__ == "__main__":
    sys.exit(main())
