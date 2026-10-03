"""Backtest v5: rebuilt signal rule, fixing the three design faults seen live.

Live journal over 115 minutes on #AAPL_otc exposed:
  1. 45 signals / 115 candles — fires almost every candle, no selection
  2. 38 CALL vs 7 PUT while entry candles were 23 up / 21 down — directional bias
  3. PUT arm barely triggers, and loses when it does

The rebuild targets those faults, not the winrate. Candidates are scored on
holdout with Wilson lower bound; selectivity and CALL/PUT balance are reported
alongside, because a rule that only fires 5% of the time and splits evenly is a
better instrument even at the same accuracy.
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"


def ema(v, p):
    out, k, acc = [], 2 / (p + 1), v[0]
    for x in v:
        acc = x * k + acc * (1 - k)
        out.append(acc)
    return out


def rsi_series(v, p=14):
    out, g, l = [50.0] * len(v), 0.0, 0.0
    for i in range(1, len(v)):
        d = v[i] - v[i - 1]
        up, dn = max(d, 0.0), max(-d, 0.0)
        if i <= p:
            g += up / p
            l += dn / p
        else:
            g = (g * (p - 1) + up) / p
            l = (l * (p - 1) + dn) / p
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def sma(v, p):
    out, run = [v[0]] * len(v), 0.0
    for i, x in enumerate(v):
        run += x
        if i >= p:
            run -= v[i - p]
        out[i] = run / min(i + 1, p)
    return out


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def build(seg):
    o = [r[1] for r in seg]
    h = [r[2] for r in seg]
    lo = [r[3] for r in seg]
    c = [r[4] for r in seg]
    n = len(seg)
    f = {"o": o, "h": h, "l": lo, "c": c, "e9": ema(c, 9), "e21": ema(c, 21),
         "e50": ema(c, 50), "rsi": rsi_series(c), "sma20": sma(c, 20),
         "rng": [h[i] - lo[i] for i in range(n)],
         "body": [abs(c[i] - o[i]) for i in range(n)]}
    f["atr"] = sma(f["rng"], 14)
    f["dev"] = sma([abs(c[i] - c[i - 1]) if i else 0.0 for i in range(n)], 20)
    return f


# ---- candidates. Each returns CALL/PUT/None. Symmetric by construction: the
# PUT branch is the exact mirror of the CALL branch, which is what the live
# rule failed to be.

def r_live(f, i):
    """The rule running in the app today, for comparison."""
    if f["e9"][i] > f["e21"][i] and f["c"][i] > f["e9"][i] and f["rsi"][i] < 72:
        return "CALL"
    if f["e9"][i] < f["e21"][i] and f["c"][i] < f["e9"][i] and f["rsi"][i] > 28:
        return "PUT"
    return None


def _z(f, i):
    return (f["c"][i] - f["sma20"][i]) / f["dev"][i] if f["dev"][i] > 0 else 0.0


def r_z2(f, i):
    z = _z(f, i)
    return "CALL" if z < -2 else "PUT" if z > 2 else None


def r_z3(f, i):
    z = _z(f, i)
    return "CALL" if z < -3 else "PUT" if z > 3 else None


def r_z4(f, i):
    z = _z(f, i)
    return "CALL" if z < -4 else "PUT" if z > 4 else None


def r_rsi_20_80(f, i):
    return "CALL" if f["rsi"][i] < 20 else "PUT" if f["rsi"][i] > 80 else None


def r_rsi_15_85(f, i):
    return "CALL" if f["rsi"][i] < 15 else "PUT" if f["rsi"][i] > 85 else None


def r_z3_and_rsi(f, i):
    """Two independent stretch measures must agree."""
    z = _z(f, i)
    if z < -3 and f["rsi"][i] < 30:
        return "CALL"
    if z > 3 and f["rsi"][i] > 70:
        return "PUT"
    return None


def r_z3_with_trend(f, i):
    """Stretch, but only with the EMA50 trend — avoids fading a real move."""
    z = _z(f, i)
    if z < -3 and f["c"][i] > f["e50"][i]:
        return "CALL"
    if z > 3 and f["c"][i] < f["e50"][i]:
        return "PUT"
    return None


def r_wick_reject(f, i):
    """Large candle rejected from its extreme: symmetric wick test."""
    if f["atr"][i] <= 0 or f["rng"][i] < f["atr"][i] * 1.6 or f["rng"][i] <= 0:
        return None
    upper = f["h"][i] - max(f["c"][i], f["o"][i])
    lower = min(f["c"][i], f["o"][i]) - f["l"][i]
    if upper > f["rng"][i] * 0.6:
        return "PUT"
    if lower > f["rng"][i] * 0.6:
        return "CALL"
    return None


def r_exhaust(f, i):
    """Streak AND stretch AND an oversized candle — deliberately rare."""
    if i < 3 or f["atr"][i] <= 0:
        return None
    z = _z(f, i)
    up3 = all(f["c"][j] > f["o"][j] for j in range(i - 2, i + 1))
    dn3 = all(f["c"][j] < f["o"][j] for j in range(i - 2, i + 1))
    big = f["rng"][i] > f["atr"][i] * 1.5
    if up3 and big and z > 2:
        return "PUT"
    if dn3 and big and z < -2:
        return "CALL"
    return None


CANDIDATES = {
    "live rule (EMA+RSI)": r_live,
    "z>2 revert": r_z2,
    "z>3 revert": r_z3,
    "z>4 revert": r_z4,
    "RSI 20/80": r_rsi_20_80,
    "RSI 15/85": r_rsi_15_85,
    "z3 + RSI agree": r_z3_and_rsi,
    "z3 + trend filter": r_z3_with_trend,
    "wick rejection": r_wick_reject,
    "exhaustion combo": r_exhaust,
}

COOLDOWN = 5  # candles a rule must stay silent after firing


def segments_of(rows, minlen=250):
    out, seg = [], [rows[0]]
    for prev, cur in zip(rows, rows[1:]):
        if cur[0] - prev[0] == 60:
            seg.append(cur)
        else:
            if len(seg) >= minlen:
                out.append(seg)
            seg = [cur]
    if len(seg) >= minlen:
        out.append(seg)
    return out


def run(limit=None, cooldown=COOLDOWN):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    symbols = [s for (s,) in con.execute(
        "SELECT symbol FROM candles_m1 GROUP BY symbol HAVING COUNT(*) >= 600 ORDER BY COUNT(*) DESC")]
    if limit:
        symbols = symbols[:limit]

    # name -> [trW,trN,hoW,hoN,calls,puts,opportunities]
    t = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])

    for sym in symbols:
        rows = con.execute(
            "SELECT time, open, high, low, close FROM candles_m1 WHERE symbol=? ORDER BY time", (sym,)).fetchall()
        for seg in segments_of(rows):
            f = build(seg)
            split = int(len(seg) * 0.6)
            last_fire = {name: -999 for name in CANDIDATES}
            for i in range(60, len(seg) - 1):
                nxt = seg[i + 1]
                if nxt[4] == nxt[1]:
                    continue
                up = nxt[4] > nxt[1]
                for name, rule in CANDIDATES.items():
                    t[name][6] += 1
                    sig = rule(f, i)
                    if sig is None:
                        continue
                    if i - last_fire[name] < cooldown:
                        continue
                    last_fire[name] = i
                    win = (sig == "CALL") == up
                    if i < split:
                        t[name][0] += win
                        t[name][1] += 1
                    else:
                        t[name][2] += win
                        t[name][3] += 1
                        t[name][4 if sig == "CALL" else 5] += 1

    print(f"symbols {len(symbols)} | cooldown {cooldown} candles | holdout = last 40%\n")
    print(f"{'rule':<24}{'hold%':>8}{'95%low':>8}{'n':>8}{'fire%':>7}{'CALL/PUT':>12}  verdict")
    print("-" * 76)
    rank = []
    for name, (tw, tn, hw, hn, ca, pu, opp) in t.items():
        if hn < 150:
            continue
        rank.append((wilson_low(hw, hn), hw / hn * 100, hn, (hn + tn) / opp * 100 if opp else 0,
                     ca, pu, name))
    rank.sort(reverse=True)
    for low, ho, hn, fire, ca, pu, name in rank:
        bal = min(ca, pu) / max(ca, pu) * 100 if max(ca, pu) else 0
        verdict = "EDGE" if low > 50 else "noise"
        print(f"{name:<24}{ho:>7.2f}%{low:>7.1f}%{hn:>8,}{fire:>6.1f}%{ca:>6,}/{pu:<5,}{bal:>4.0f}% {verdict}")
    print("\nfire% = share of candles that produced a signal (live rule was ~39%)")
    print("CALL/PUT balance: 100% = perfectly symmetric (live rule was 18%)")
    con.close()


if __name__ == "__main__":
    run(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
