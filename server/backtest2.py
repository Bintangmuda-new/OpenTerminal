"""Backtest v2: raw winrate hunt, no payout filter.

Thinking like the house: an OTC feed is generated, not traded. If an edge exists
it lives in repeatable behaviour — a specific symbol, a specific hour, or the
reaction to an extreme event — not in a generic indicator average.

Discipline kept from v1: train (first 60%) picks, holdout (last 40%) reports.
A rule that only wins on train is noise, however good the number looks.
"""
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
MIN_TRAIN = 120
MIN_HOLD = 80


def ema(values, period):
    out, k, acc = [], 2 / (period + 1), values[0]
    for v in values:
        acc = v * k + acc * (1 - k)
        out.append(acc)
    return out


def rsi(values, period=14):
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


def features(rows):
    o = [r[1] for r in rows]
    h = [r[2] for r in rows]
    l = [r[3] for r in rows]
    c = [r[4] for r in rows]
    n = len(rows)
    f = {"t": [r[0] for r in rows], "o": o, "h": h, "l": l, "c": c,
         "e9": ema(c, 9), "e21": ema(c, 21), "e50": ema(c, 50), "rsi": rsi(c),
         "sma20": sma(c, 20),
         "rng": [h[i] - l[i] for i in range(n)],
         "body": [abs(c[i] - o[i]) for i in range(n)]}
    f["atr"] = sma(f["rng"], 14)
    f["vol"] = sma([abs(c[i] - c[i - 1]) if i else 0.0 for i in range(n)], 20)
    return f


# --- Sharper, rarer rules. The house leaks on extremes, not on averages. ---

def r_current(f, i):
    if f["e9"][i] > f["e21"][i] and f["c"][i] > f["e9"][i] and f["rsi"][i] < 72:
        return "CALL"
    if f["e9"][i] < f["e21"][i] and f["c"][i] < f["e9"][i] and f["rsi"][i] > 28:
        return "PUT"
    return None


def r_rsi_extreme(f, i):
    if f["rsi"][i] < 18:
        return "CALL"
    if f["rsi"][i] > 82:
        return "PUT"
    return None


def r_rsi_extreme_x(f, i):
    if f["rsi"][i] < 12:
        return "CALL"
    if f["rsi"][i] > 88:
        return "PUT"
    return None


def r_dev_revert(f, i):
    if f["vol"][i] <= 0:
        return None
    dev = (f["c"][i] - f["sma20"][i]) / f["vol"][i]
    if dev < -3.5:
        return "CALL"
    if dev > 3.5:
        return "PUT"
    return None


def r_dev_revert_x(f, i):
    if f["vol"][i] <= 0:
        return None
    dev = (f["c"][i] - f["sma20"][i]) / f["vol"][i]
    if dev < -5:
        return "CALL"
    if dev > 5:
        return "PUT"
    return None


def r_streak5_fade(f, i):
    if i < 5:
        return None
    if all(f["c"][j] > f["o"][j] for j in range(i - 4, i + 1)):
        return "PUT"
    if all(f["c"][j] < f["o"][j] for j in range(i - 4, i + 1)):
        return "CALL"
    return None


def r_streak5_follow(f, i):
    s = r_streak5_fade(f, i)
    return None if s is None else ("CALL" if s == "PUT" else "PUT")


def r_streak4_fade(f, i):
    if i < 4:
        return None
    if all(f["c"][j] > f["o"][j] for j in range(i - 3, i + 1)):
        return "PUT"
    if all(f["c"][j] < f["o"][j] for j in range(i - 3, i + 1)):
        return "CALL"
    return None


def r_huge_fade(f, i):
    if f["atr"][i] <= 0 or f["rng"][i] < f["atr"][i] * 3:
        return None
    return "PUT" if f["c"][i] > f["o"][i] else "CALL"


def r_huge_follow(f, i):
    s = r_huge_fade(f, i)
    return None if s is None else ("CALL" if s == "PUT" else "PUT")


def r_pin_fade(f, i):
    """Long wick = rejection; bet the rejection holds."""
    if f["rng"][i] <= 0 or f["atr"][i] <= 0 or f["rng"][i] < f["atr"][i] * 1.5:
        return None
    upper = f["h"][i] - max(f["c"][i], f["o"][i])
    lower = min(f["c"][i], f["o"][i]) - f["l"][i]
    if upper > f["rng"][i] * 0.65:
        return "PUT"
    if lower > f["rng"][i] * 0.65:
        return "CALL"
    return None


def r_doji_break(f, i):
    if f["rng"][i] <= 0 or f["body"][i] > f["rng"][i] * 0.1:
        return None
    return "CALL" if f["c"][i] > f["sma20"][i] else "PUT"


def r_gap_revert(f, i):
    """Open far from previous close — synthetic feeds often pull it back."""
    if i < 1 or f["atr"][i] <= 0:
        return None
    gap = f["o"][i] - f["c"][i - 1]
    if gap > f["atr"][i] * 0.8:
        return "PUT"
    if gap < -f["atr"][i] * 0.8:
        return "CALL"
    return None


def r_double_extreme(f, i):
    """Two stretched closes in a row: stronger exhaustion claim."""
    if i < 1:
        return None
    if f["rsi"][i] < 25 and f["rsi"][i - 1] < 25:
        return "CALL"
    if f["rsi"][i] > 75 and f["rsi"][i - 1] > 75:
        return "PUT"
    return None


def r_always_call(f, i):
    return "CALL"


RULES = {
    "current(EMA+RSI)": r_current,
    "RSI<18/>82": r_rsi_extreme,
    "RSI<12/>88": r_rsi_extreme_x,
    "dev revert 3.5": r_dev_revert,
    "dev revert 5.0": r_dev_revert_x,
    "fade 4-streak": r_streak4_fade,
    "fade 5-streak": r_streak5_fade,
    "follow 5-streak": r_streak5_follow,
    "fade 3xATR candle": r_huge_fade,
    "follow 3xATR candle": r_huge_follow,
    "pin bar fade": r_pin_fade,
    "doji break": r_doji_break,
    "gap revert": r_gap_revert,
    "double RSI extreme": r_double_extreme,
    "control: always CALL": r_always_call,
}


def segments_of(rows):
    out, seg = [], [rows[0]]
    for prev, cur in zip(rows, rows[1:]):
        if cur[0] - prev[0] == 60:
            seg.append(cur)
        else:
            if len(seg) >= 200:
                out.append(seg)
            seg = [cur]
    if len(seg) >= 200:
        out.append(seg)
    return out


def run(limit=None, per_symbol=True, by_hour=True):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    symbols = [s for (s,) in con.execute(
        "SELECT symbol FROM candles_m1 GROUP BY symbol HAVING COUNT(*) >= 800 ORDER BY COUNT(*) DESC")]
    import random; random.seed(7); random.shuffle(symbols)
    if limit:
        symbols = symbols[:limit]
    print(f"symbols {len(symbols)} | train=first60% holdout=last40% | raw winrate, payout ignored\n")

    glob = defaultdict(lambda: [0, 0, 0, 0])          # rule -> trW,trN,hoW,hoN
    persym = defaultdict(lambda: [0, 0, 0, 0])        # (rule,sym)
    perhour = defaultdict(lambda: [0, 0, 0, 0])       # (rule,hour)

    for sym in symbols:
        rows = con.execute(
            "SELECT time, open, high, low, close FROM candles_m1 WHERE symbol=? ORDER BY time", (sym,)).fetchall()
        for seg in segments_of(rows):
            f = features(seg)
            split = int(len(seg) * 0.6)
            for i in range(60, len(seg) - 1):
                nxt = seg[i + 1]
                if nxt[4] == nxt[1]:
                    continue
                up = nxt[4] > nxt[1]
                train = i < split
                hour = datetime.fromtimestamp(seg[i][0], timezone.utc).hour
                for name, rule in RULES.items():
                    sig = rule(f, i)
                    if sig is None:
                        continue
                    win = (sig == "CALL") == up
                    for bucket in (glob[name], persym[(name, sym)], perhour[(name, hour)]):
                        if train:
                            bucket[0] += win
                            bucket[1] += 1
                        else:
                            bucket[2] += win
                            bucket[3] += 1

    def table(title, items, keyfmt, min_tr=MIN_TRAIN, min_ho=MIN_HOLD, top=15):
        print(f"\n=== {title} ===")
        rank = []
        for k, (tw, tn, hw, hn) in items:
            if tn < min_tr or hn < min_ho:
                continue
            rank.append((hw / hn * 100, tw / tn * 100, hn, k))
        rank.sort(reverse=True)
        print(f"{'':<34}{'holdout%':>10}{'train%':>9}{'trades':>9}  gap")
        for ho, tr, hn, k in rank[:top]:
            flag = "STABLE" if abs(ho - tr) < 2 and ho > 52 else ""
            print(f"{keyfmt(k):<34}{ho:>9.2f}%{tr:>8.2f}%{hn:>9,}  {ho-tr:+.1f} {flag}")
        return rank

    table("ALL SYMBOLS COMBINED", glob.items(), lambda k: k, MIN_TRAIN, MIN_HOLD, 20)
    if per_symbol:
        table("BEST RULE x SYMBOL", persym.items(), lambda k: f"{k[0]} @ {k[1]}", 150, 100, 25)
    if by_hour:
        table("BEST RULE x HOUR (UTC)", perhour.items(), lambda k: f"{k[0]} @ {k[1]:02d}h", 200, 150, 20)
    con.close()


if __name__ == "__main__":
    run(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
