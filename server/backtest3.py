"""Backtest v3: does the streak-fade edge actually hold?

Three tests that separate a real edge from a lucky sample:
  1. all symbols, not five
  2. CALL and PUT scored separately — a rule that only wins one way is riding
     a trend, not finding an edge
  3. streak lengths 3..8 — a real mean-reversion edge gets STRONGER as the
     streak gets longer. A flat or noisy gradient means the 5-streak result
     was coincidence.
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"


def segments_of(rows):
    out, seg = [], [rows[0]]
    for prev, cur in zip(rows, rows[1:]):
        if cur[0] - prev[0] == 60:
            seg.append(cur)
        else:
            if len(seg) >= 120:
                out.append(seg)
            seg = [cur]
    if len(seg) >= 120:
        out.append(seg)
    return out


def wilson_low(wins, n, z=1.96):
    """Lower bound of the 95% CI. If this stays above 50 the edge survives
    sampling noise; a bare percentage does not tell you that."""
    if n == 0:
        return 0.0
    p = wins / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (centre - margin) / d * 100


def run(limit=None):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    symbols = [s for (s,) in con.execute(
        "SELECT symbol FROM candles_m1 GROUP BY symbol HAVING COUNT(*) >= 400 ORDER BY COUNT(*) DESC")]
    if limit:
        symbols = symbols[:limit]

    # (streak, phase, direction) -> [wins, n]
    tally = defaultdict(lambda: [0, 0])
    # (streak, symbol) -> [wins, n] on holdout, to count how many symbols agree
    bysym = defaultdict(lambda: [0, 0])
    drift = [0, 0]

    for sym in symbols:
        rows = con.execute(
            "SELECT time, open, high, low, close FROM candles_m1 WHERE symbol=? ORDER BY time", (sym,)).fetchall()
        for seg in segments_of(rows):
            o = [r[1] for r in seg]
            c = [r[4] for r in seg]
            split = int(len(seg) * 0.6)
            for i in range(8, len(seg) - 1):
                nxt = seg[i + 1]
                if nxt[4] == nxt[1]:
                    continue
                up = nxt[4] > nxt[1]
                phase = "train" if i < split else "hold"
                if phase == "hold":
                    drift[0] += up
                    drift[1] += 1
                for k in range(3, 9):
                    green = all(c[j] > o[j] for j in range(i - k + 1, i + 1))
                    red = all(c[j] < o[j] for j in range(i - k + 1, i + 1))
                    if not green and not red:
                        continue
                    sig = "PUT" if green else "CALL"   # fade the streak
                    win = (sig == "CALL") == up
                    tally[(k, phase, sig)][0] += win
                    tally[(k, phase, sig)][1] += 1
                    tally[(k, phase, "BOTH")][0] += win
                    tally[(k, phase, "BOTH")][1] += 1
                    if phase == "hold":
                        bysym[(k, sym)][0] += win
                        bysym[(k, sym)][1] += 1

    print(f"symbols {len(symbols)} | fade-the-streak | holdout = last 40%")
    print(f"baseline up-move rate in holdout: {drift[0]/drift[1]*100:.2f}%  "
          f"({drift[1]:,} candles)\n")

    print("TEST 3 — gradient: a real mean-reversion edge strengthens with length")
    print(f"{'streak':>7}{'train%':>9}{'hold%':>9}{'hold n':>10}{'95% low':>9}   verdict")
    print("-" * 62)
    for k in range(3, 9):
        tw, tn = tally[(k, "train", "BOTH")]
        hw, hn = tally[(k, "hold", "BOTH")]
        if tn == 0 or hn == 0:
            continue
        low = wilson_low(hw, hn)
        verdict = "survives noise" if low > 50 else "within noise"
        print(f"{k:>7}{tw/tn*100:>8.2f}%{hw/hn*100:>8.2f}%{hn:>10,}{low:>8.1f}%   {verdict}")

    print("\nTEST 2 — direction split (trend-riding shows up as one-sided)")
    print(f"{'streak':>7}{'CALL%':>9}{'n':>9}{'PUT%':>9}{'n':>9}   balance")
    print("-" * 56)
    for k in range(3, 9):
        cw, cn = tally[(k, "hold", "CALL")]
        pw, pn = tally[(k, "hold", "PUT")]
        if cn == 0 or pn == 0:
            continue
        cr, pr = cw / cn * 100, pw / pn * 100
        bal = "both sides" if min(cr, pr) > 50 else "ONE-SIDED"
        print(f"{k:>7}{cr:>8.2f}%{cn:>9,}{pr:>8.2f}%{pn:>9,}   {bal}")

    print("\nTEST 1 — breadth: how many symbols agree (holdout, n>=40 each)")
    print(f"{'streak':>7}{'symbols':>9}{'>50%':>7}{'share':>8}")
    print("-" * 33)
    for k in range(3, 9):
        rates = [w / n for (kk, _), (w, n) in bysym.items() if kk == k and n >= 40]
        if not rates:
            continue
        good = sum(1 for r in rates if r > 0.5)
        print(f"{k:>7}{len(rates):>9}{good:>7}{good/len(rates)*100:>7.0f}%")

    con.close()


if __name__ == "__main__":
    run(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
