"""Backtest v4: NEXT ENTRY (confluence score) vs CALL/PUT (EMA+RSI) vs both agreeing.

Ports ConfluenceScore + CompositeScore + AnalyzeStructure from MainWindow.xaml.cs
so the two live signal systems are measured on the same footing, plus the
collaboration case where both must agree before a trade counts.

Read-only. Train = first 60%, holdout = last 40%; only holdout is reported.
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"


def ema(values, period):
    out, k, acc = [], 2 / (period + 1), values[0]
    for v in values:
        acc = v * k + acc * (1 - k)
        out.append(acc)
    return out


def rsi_series(values, period=14):
    out, gain, loss = [50.0] * len(values), 0.0, 0.0
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if i <= period:
            gain += g / period
            loss += l / period
        else:
            gain = (gain * (period - 1) + g) / period
            loss = (loss * (period - 1) + l) / period
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def sma(values, period):
    out, run = [values[0]] * len(values), 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        out[i] = run / min(i + 1, period)
    return out


def wilson_low(wins, n, z=1.96):
    if n == 0:
        return 0.0
    p = wins / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (centre - margin) / d * 100


def build(seg):
    o = [r[1] for r in seg]
    h = [r[2] for r in seg]
    l = [r[3] for r in seg]
    c = [r[4] for r in seg]
    n = len(seg)
    f = {"o": o, "h": h, "l": l, "c": c,
         "e9": ema(c, 9), "e21": ema(c, 21), "e50": ema(c, 50), "e200": ema(c, 200),
         "rsi": rsi_series(c), "sma20": sma(c, 20),
         "rng": [h[i] - l[i] for i in range(n)],
         "body": [abs(c[i] - o[i]) for i in range(n)]}
    f["atr"] = sma(f["rng"], 14)
    # cumulative VWAP over typical price, matching CumulativeVwap
    vw, acc = [], 0.0
    for i in range(n):
        acc += (h[i] + l[i] + c[i]) / 3
        vw.append(acc / (i + 1))
    f["vwap"] = vw
    fast12, slow26 = ema(c, 12), ema(c, 26)
    diff = [fast12[i] - slow26[i] for i in range(n)]
    sig = ema(diff, 9)
    f["macd_hist"] = [diff[i] - sig[i] for i in range(n)]
    return f


def structure_events(f, upto):
    """Ports AnalyzeStructure: swing pivots then BOS/CHoCH on close breaks."""
    events, swings = [], []
    last_high = last_low = None
    trend = "RANGE"
    for i in range(upto):
        p = i - 1
        if p >= 1:
            if f["h"][p] > max(f["h"][p - 1], f["h"][p + 1] if p + 1 < upto else f["h"][p - 1]):
                last_high = (p, f["h"][p])
                swings.append(("H",) + last_high)
            if f["l"][p] < min(f["l"][p - 1], f["l"][p + 1] if p + 1 < upto else f["l"][p - 1]):
                last_low = (p, f["l"][p])
                swings.append(("L",) + last_low)
        if i == 0:
            continue
        prev_c, cur_c = f["c"][i - 1], f["c"][i]
        if last_high and prev_c <= last_high[1] < cur_c:
            events.append((i, "UP", last_high[1], "CHoCH" if trend == "DOWN" else "BOS"))
            trend, last_high = "UP", None
        elif last_low and prev_c >= last_low[1] > cur_c:
            events.append((i, "DOWN", last_low[1], "CHoCH" if trend == "UP" else "BOS"))
            trend, last_low = "DOWN", None
    return events


def pattern_of(f, i):
    c, o, h, l = f["c"][i], f["o"][i], f["h"][i], f["l"][i]
    pc, po = f["c"][i - 1], f["o"][i - 1]
    rng, body = h - l, abs(c - o)
    if rng <= 0:
        return ""
    if c > o and pc < po and c >= po and o <= pc:
        return "BullishEngulfing"
    if c < o and pc > po and c <= po and o >= pc:
        return "BearishEngulfing"
    if body < rng * 0.35 and (min(c, o) - l) > rng * 0.5:
        return "Hammer"
    if body < rng * 0.35 and (h - max(c, o)) > rng * 0.5:
        return "ShootingStar"
    if body > rng * 0.8:
        return "MarubozuUp" if c > o else "MarubozuDown"
    return ""


def confluence(f, i, direction, level, retest_tol):
    s = 0
    bull = direction == "UP"
    c, o = f["c"][i], f["o"][i]
    body, rng, atr = f["body"][i], f["rng"][i], f["atr"][i]
    breakout = body / rng if rng > 0 else 0
    ema_gap = abs(f["e9"][i] - f["e21"][i])
    if (c > level) if bull else (c < level):
        s += 20
    if (c > o and f["e9"][i] > f["e21"][i] and c > f["e50"][i]) if bull else (c < o and f["e9"][i] < f["e21"][i] and c < f["e50"][i]):
        s += 15
    if (c > f["sma20"][i]) if bull else (c < f["sma20"][i]):
        s += 10
    if rng >= atr * 1.1:
        s += 10
    if body >= atr * 0.6:
        s += 10
    if breakout >= 0.55:
        s += 10
    if (50 <= f["rsi"][i] <= 70) if bull else (30 <= f["rsi"][i] <= 50):
        s += 10
    if ema_gap >= max(atr * 0.25, abs(c) * 8e-6):
        s += 5
    if (c > f["vwap"][i]) if bull else (c < f["vwap"][i]):
        s += 5
    if (f["l"][i] <= level + retest_tol) if bull else (f["h"][i] >= level - retest_tol):
        s += 5
    p = pattern_of(f, i)
    if p in (("BullishEngulfing", "Hammer", "MarubozuUp") if bull else ("BearishEngulfing", "ShootingStar", "MarubozuDown")):
        s += 10
    # CompositeScore
    if (c > f["e200"][i]) if bull else (c < f["e200"][i]):
        s += 5
    if (f["macd_hist"][i] > 0) if bull else (f["macd_hist"][i] < 0):
        s += 5
    slope = f["vwap"][i] - f["vwap"][i - 1] if i else 0
    if (slope > 0 and c > f["vwap"][i]) if bull else (slope < 0 and c < f["vwap"][i]):
        s += 5
    return min(s, 100)


def emarsi_signal(f, i):
    if f["e9"][i] > f["e21"][i] and f["c"][i] > f["e9"][i] and f["rsi"][i] < 72:
        return "CALL"
    if f["e9"][i] < f["e21"][i] and f["c"][i] < f["e9"][i] and f["rsi"][i] > 28:
        return "PUT"
    return None


def segments_of(rows):
    out, seg = [], [rows[0]]
    for prev, cur in zip(rows, rows[1:]):
        if cur[0] - prev[0] == 60:
            seg.append(cur)
        else:
            if len(seg) >= 250:
                out.append(seg)
            seg = [cur]
    if len(seg) >= 250:
        out.append(seg)
    return out


def run(limit=None, thresholds=(70, 80, 90)):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    symbols = [s for (s,) in con.execute(
        "SELECT symbol FROM candles_m1 GROUP BY symbol HAVING COUNT(*) >= 600 ORDER BY COUNT(*) DESC")]
    if limit:
        symbols = symbols[:limit]

    tally = defaultdict(lambda: [0, 0, 0, 0])  # name -> trW,trN,hoW,hoN

    for sym in symbols:
        rows = con.execute(
            "SELECT time, open, high, low, close FROM candles_m1 WHERE symbol=? ORDER BY time", (sym,)).fetchall()
        for seg in segments_of(rows):
            f = build(seg)
            split = int(len(seg) * 0.6)
            events = structure_events(f, len(seg) - 1)
            ev_at = {}
            for idx, direction, level, kind in events:
                if kind in ("BOS", "CHoCH"):
                    ev_at[idx] = (direction, level, kind)

            for i in range(210, len(seg) - 1):
                nxt = seg[i + 1]
                if nxt[4] == nxt[1]:
                    continue
                up = nxt[4] > nxt[1]
                phase = 0 if i < split else 2

                def record(name, sig):
                    win = (sig == "CALL") == up
                    tally[name][phase] += win
                    tally[name][phase + 1] += 1

                er = emarsi_signal(f, i)
                if er:
                    record("CALL/PUT (EMA+RSI)", er)

                ev = ev_at.get(i)
                nxt_sig = None
                if ev:
                    direction, level, kind = ev
                    atr = f["atr"][i]
                    if atr > 0 and f["rng"][i] >= atr * 1.05:
                        tol = max(atr * 0.35, abs(f["c"][i]) * 1e-5)
                        score = confluence(f, i, direction, level, tol)
                        nxt_sig = "CALL" if direction == "UP" else "PUT"
                        for th in thresholds:
                            if score >= th:
                                record(f"NEXT ENTRY score>={th}", nxt_sig)
                        if score >= 80 and er:
                            record("BOTH AGREE (score>=80)" if er == nxt_sig else "BOTH DISAGREE (score>=80)",
                                   nxt_sig if er == nxt_sig else er)

    print(f"symbols {len(symbols)} | holdout = last 40% | raw winrate\n")
    print(f"{'system':<30}{'train%':>9}{'hold%':>9}{'hold n':>10}{'95% low':>10}   verdict")
    print("-" * 78)
    rank = []
    for name, (tw, tn, hw, hn) in tally.items():
        if hn < 60:
            continue
        rank.append((wilson_low(hw, hn), tw / tn * 100 if tn else 0, hw / hn * 100, hn, name))
    rank.sort(reverse=True)
    for low, tr, ho, hn, name in rank:
        verdict = "EDGE SURVIVES" if low > 50 else "within noise"
        print(f"{name:<30}{tr:>8.2f}%{ho:>8.2f}%{hn:>10,}{low:>9.1f}%   {verdict}")
    con.close()


if __name__ == "__main__":
    run(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
