"""Backtest 1M binary-option entry rules against the captured OTC candles.

Read-only against data/otc_research.sqlite3. Entry is evaluated on candle i and
resolved on candle i+1 (close vs open), matching how the terminal signals:
the alert fires before candle i closes and the order runs on the next one.

Discipline against overfitting:
  * rules are split train (first 60%) / holdout (last 40%) per symbol
  * ranking happens on train only; the reported number is the holdout result
  * break-even at 92% payout is 52.08%, printed alongside every rule
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
PAYOUT = 0.92
BREAK_EVEN = 1 / (1 + PAYOUT) * 100
MIN_TRADES = 200


def ema(values, period):
    out, k = [], 2 / (period + 1)
    acc = values[0]
    for v in values:
        acc = v * k + acc * (1 - k)
        out.append(acc)
    return out


def rsi(values, period=14):
    out = [50.0] * len(values)
    gain = loss = 0.0
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
    close = [r[4] for r in rows]
    o = [r[1] for r in rows]
    hi = [r[2] for r in rows]
    lo = [r[3] for r in rows]
    n = len(rows)
    f = {
        "o": o, "h": hi, "l": lo, "c": close,
        "e9": ema(close, 9), "e21": ema(close, 21), "e50": ema(close, 50),
        "rsi": rsi(close), "sma20": sma(close, 20),
        "body": [abs(close[i] - o[i]) for i in range(n)],
        "rng": [hi[i] - lo[i] for i in range(n)],
    }
    f["atr"] = sma(f["rng"], 14)
    # rolling stdev proxy for Bollinger-style squeeze detection
    f["vol"] = sma([abs(close[i] - close[i - 1]) if i else 0.0 for i in range(n)], 20)
    return f


# Each rule returns "CALL", "PUT" or None for index i, using only data up to i.
def r_current(f, i):
    """The rule shipping in the terminal today: EMA9/21 + price vs EMA9 + RSI band."""
    if f["e9"][i] > f["e21"][i] and f["c"][i] > f["e9"][i] and f["rsi"][i] < 72:
        return "CALL"
    if f["e9"][i] < f["e21"][i] and f["c"][i] < f["e9"][i] and f["rsi"][i] > 28:
        return "PUT"
    return None


def r_ema_cross(f, i):
    if i < 1:
        return None
    if f["e9"][i - 1] <= f["e21"][i - 1] and f["e9"][i] > f["e21"][i]:
        return "CALL"
    if f["e9"][i - 1] >= f["e21"][i - 1] and f["e9"][i] < f["e21"][i]:
        return "PUT"
    return None


def r_mean_revert(f, i):
    """OTC synthetic feeds tend to mean-revert; fade stretched RSI."""
    if f["rsi"][i] < 25:
        return "CALL"
    if f["rsi"][i] > 75:
        return "PUT"
    return None


def r_mean_revert_soft(f, i):
    if f["rsi"][i] < 35:
        return "CALL"
    if f["rsi"][i] > 65:
        return "PUT"
    return None


def r_fade_big_candle(f, i):
    """Fade an outsized candle: exhaustion bet."""
    if f["atr"][i] <= 0 or f["rng"][i] < f["atr"][i] * 1.8:
        return None
    return "PUT" if f["c"][i] > f["o"][i] else "CALL"


def r_follow_big_candle(f, i):
    if f["atr"][i] <= 0 or f["rng"][i] < f["atr"][i] * 1.8:
        return None
    return "CALL" if f["c"][i] > f["o"][i] else "PUT"


def r_streak3_fade(f, i):
    if i < 3:
        return None
    up = all(f["c"][j] > f["o"][j] for j in (i, i - 1, i - 2))
    dn = all(f["c"][j] < f["o"][j] for j in (i, i - 1, i - 2))
    if up:
        return "PUT"
    if dn:
        return "CALL"
    return None


def r_streak3_follow(f, i):
    s = r_streak3_fade(f, i)
    return None if s is None else ("CALL" if s == "PUT" else "PUT")


def r_bb_revert(f, i):
    """Price far from SMA20 relative to recent movement -> revert."""
    if f["vol"][i] <= 0:
        return None
    dev = (f["c"][i] - f["sma20"][i]) / f["vol"][i]
    if dev < -2.5:
        return "CALL"
    if dev > 2.5:
        return "PUT"
    return None


def r_trend_pullback(f, i):
    """Trade with EMA50 trend, entering on a pullback against it."""
    if f["c"][i] > f["e50"][i] and f["c"][i] < f["e9"][i] and f["rsi"][i] < 45:
        return "CALL"
    if f["c"][i] < f["e50"][i] and f["c"][i] > f["e9"][i] and f["rsi"][i] > 55:
        return "PUT"
    return None


def r_revert_plus_trend(f, i):
    """Mean reversion, but only in the direction of the EMA50 trend."""
    s = r_mean_revert_soft(f, i)
    if s is None:
        return None
    if s == "CALL" and f["c"][i] > f["e50"][i]:
        return "CALL"
    if s == "PUT" and f["c"][i] < f["e50"][i]:
        return "PUT"
    return None


def r_inside_bar(f, i):
    if i < 1:
        return None
    if f["h"][i] < f["h"][i - 1] and f["l"][i] > f["l"][i - 1]:
        return "CALL" if f["c"][i] > f["o"][i] else "PUT"
    return None


def r_always_call(f, i):
    """Control: exposes any directional drift in the data itself."""
    return "CALL"


RULES = {
    "current (EMA9/21+RSI)": r_current,
    "ema cross": r_ema_cross,
    "mean revert RSI 25/75": r_mean_revert,
    "mean revert RSI 35/65": r_mean_revert_soft,
    "fade big candle": r_fade_big_candle,
    "follow big candle": r_follow_big_candle,
    "fade 3-streak": r_streak3_fade,
    "follow 3-streak": r_streak3_follow,
    "bb revert 2.5sd": r_bb_revert,
    "trend pullback": r_trend_pullback,
    "revert+trend filter": r_revert_plus_trend,
    "inside bar": r_inside_bar,
    "control: always CALL": r_always_call,
}


def run(limit_symbols=None):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    symbols = [
        s for (s,) in con.execute(
            "SELECT symbol FROM candles_m1 GROUP BY symbol HAVING COUNT(*) >= 500 ORDER BY COUNT(*) DESC"
        )
    ]
    if limit_symbols:
        symbols = symbols[:limit_symbols]
    print(f"symbols: {len(symbols)}  payout {PAYOUT}  break-even {BREAK_EVEN:.2f}%\n")

    tally = defaultdict(lambda: {"tr_w": 0, "tr_n": 0, "ho_w": 0, "ho_n": 0})

    for sym in symbols:
        rows = con.execute(
            "SELECT time, open, high, low, close FROM candles_m1 WHERE symbol = ? ORDER BY time", (sym,)
        ).fetchall()
        # Split on gaps: a missing minute breaks candle continuity.
        segments, seg = [], [rows[0]]
        for prev, cur in zip(rows, rows[1:]):
            if cur[0] - prev[0] == 60:
                seg.append(cur)
            else:
                if len(seg) >= 200:
                    segments.append(seg)
                seg = [cur]
        if len(seg) >= 200:
            segments.append(seg)

        for seg in segments:
            f = features(seg)
            split = int(len(seg) * 0.6)
            for i in range(60, len(seg) - 1):
                nxt = seg[i + 1]
                if nxt[4] == nxt[1]:
                    continue  # flat next candle is a broker tie, not a win
                went_up = nxt[4] > nxt[1]
                phase_w, phase_n = ("tr_w", "tr_n") if i < split else ("ho_w", "ho_n")
                for name, rule in RULES.items():
                    sig = rule(f, i)
                    if sig is None:
                        continue
                    tally[name][phase_n] += 1
                    if (sig == "CALL") == went_up:
                        tally[name][phase_w] += 1

    ranked = []
    for name, t in tally.items():
        if t["tr_n"] < MIN_TRADES or t["ho_n"] < MIN_TRADES:
            continue
        ranked.append((t["tr_w"] / t["tr_n"] * 100, t["ho_w"] / t["ho_n"] * 100, name, t))
    ranked.sort(reverse=True)

    print(f"{'rule':<26}{'train%':>9}{'holdout%':>10}{'trades(ho)':>12}  verdict")
    print("-" * 72)
    for tr, ho, name, t in ranked:
        verdict = "PROFITABLE" if ho > BREAK_EVEN else "loses"
        print(f"{name:<26}{tr:>8.2f}%{ho:>9.2f}%{t['ho_n']:>12,}  {verdict}")

    print()
    if ranked:
        best_train = max(ranked, key=lambda r: r[0])
        print(f"best on TRAIN:   {best_train[2]}  ({best_train[0]:.2f}%)")
        print(f"  its HOLDOUT:   {best_train[1]:.2f}%   <- the honest number")
        if best_train[1] <= BREAK_EVEN:
            print(f"  below break-even {BREAK_EVEN:.2f}% — not tradable as-is")
    con.close()


if __name__ == "__main__":
    run(limit_symbols=int(sys.argv[1]) if len(sys.argv) > 1 else None)
