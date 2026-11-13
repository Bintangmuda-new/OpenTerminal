"""backtest15_github.py — strategi binary option publik dari GitHub, diuji jujur.

Sumber (dicari via `gh search repos`, 18 Okt 2026):
  ADMusab12/OTC-Signal-Engine      EMA9/EMA20 + RSI band, skip RSI ekstrem
  walicard56/backtest-iq-option    RSI6 + CCI fade setelah candle searah (MFI dibuang: feed tanpa volume)
  richmelody15/catalyst-signals    liquidity sweep: tembus swing lalu close balik
  pola umum "1-minute strategy"    Bollinger touch + RSI, Stochastic 14/3 cross di OB/OS

Semua sinyal dari candle A tertutup, dinilai pada candle B (close B vs open B).
Fold per hari + per simbol memakai `backtest9_walkforward` (read-only DB).
"""
import numpy as np

from backtest9_walkforward import collect, load, report, sma


def ema(v, p):
    out = np.empty_like(v)
    k = 2 / (p + 1)
    out[0] = v[0]
    for i in range(1, len(v)):
        out[i] = v[i] * k + out[i - 1] * (1 - k)
    return out


def rsi(v, p):
    d = np.diff(v, prepend=v[0])
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = ema(up, 2 * p - 1), ema(dn, 2 * p - 1)  # Wilder = EMA(2p-1)
    return np.where(al > 0, 100 - 100 / (1 + ag / np.maximum(al, 1e-12)), 100.0)


def rolling(v, p, fn):
    out = np.full_like(v, np.nan)
    for i in range(p - 1, len(v)):
        out[i] = fn(v[i - p + 1 : i + 1])
    return out


def cci(f, p=14):
    tp = (f["h"] + f["l"] + f["c"]) / 3
    m = sma(tp, p)
    md = rolling(tp, p, lambda w: np.abs(w - w.mean()).mean())
    return np.where(md > 0, (tp - m) / (0.015 * md), 0.0)


def prev(a, k=1, fill=np.nan):
    return np.concatenate([np.full(k, fill), a[:-k]])


def otc_signal_engine(f):
    c = f["c"]
    e9, e20, r = ema(c, 9), ema(c, 20), f["rsi"]
    ok = (r >= 20) & (r <= 80)
    buy = ok & (c > e9) & (e9 > e20) & (r >= 40) & (r <= 70)
    sell = ok & (c < e9) & (e9 < e20) & (r >= 30) & (r <= 60)
    return np.where(buy, 1.0, np.where(sell, -1.0, 0.0))


def iq_rsi_cci(f):
    r6, cc = rsi(f["c"], 6), cci(f)
    green, red = f["c"] > f["o"], f["c"] < f["o"]
    put = (r6 > 68) & (cc < 80) & green
    call = (r6 < 40) & (cc > -75) & red
    return np.where(call, 1.0, np.where(put, -1.0, 0.0))


def liquidity_sweep(f, look=20):
    hh = prev(rolling(f["h"], look, np.max))
    ll = prev(rolling(f["l"], look, np.min))
    put = (f["h"] > hh) & (f["c"] < hh)
    call = (f["l"] < ll) & (f["c"] > ll)
    return np.where(call, 1.0, np.where(put, -1.0, 0.0))


def bollinger_rsi(f):
    c = f["c"]
    mid, sd = sma(c, 20), rolling(c, 20, np.std)
    call = (f["l"] <= mid - 2 * sd) & (f["rsi"] < 30)
    put = (f["h"] >= mid + 2 * sd) & (f["rsi"] > 70)
    return np.where(call, 1.0, np.where(put, -1.0, 0.0))


def stoch_cross(f, k=14, d=3):
    hh, ll = rolling(f["h"], k, np.max), rolling(f["l"], k, np.min)
    pk = np.where(hh > ll, (f["c"] - ll) / np.maximum(hh - ll, 1e-12) * 100, 50.0)
    pd = sma(pk, d)
    up = (pk > pd) & (prev(pk) <= prev(pd))
    dn = (pk < pd) & (prev(pk) >= prev(pd))
    call = up & (pd < 20)
    put = dn & (pd > 80)
    return np.where(call, 1.0, np.where(put, -1.0, 0.0))


RULES = {
    "OTC-Signal-Engine EMA9/20+RSI": otc_signal_engine,
    "IQ RSI6+CCI fade": iq_rsi_cci,
    "catalyst liquidity sweep": liquidity_sweep,
    "Bollinger touch + RSI30/70": bollinger_rsi,
    "Stochastic 14/3 cross OB/OS": stoch_cross,
    "kontrol: always CALL": lambda f: np.ones(f["n"]),
}


def main():
    frames = load()
    print(f"symbols={len(frames)} bars={sum(f['n'] for f in frames)}")
    for name, rule in RULES.items():
        report(name, collect(frames, rule))


def _selftest():
    # Sinyal candle i tidak boleh berubah bila candle terakhir (masa depan) diubah.
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 0.1, 300))
    o = np.concatenate([[c[0]], c[:-1]])
    f = {"o": o, "h": np.maximum(o, c) + 0.05, "l": np.minimum(o, c) - 0.05, "c": c, "n": 300}
    f["rsi"] = rsi(c, 14)
    base = {k: r(f)[:-1] for k, r in RULES.items()}
    f2 = {**f, "c": c.copy(), "h": f["h"].copy(), "l": f["l"].copy()}
    f2["c"][-1] += 5
    f2["h"][-1] += 5
    f2["l"][-1] -= 5
    f2["rsi"] = rsi(f2["c"], 14)
    for k, r in RULES.items():
        assert np.array_equal(base[k], r(f2)[:-1], equal_nan=True), f"lookahead: {k}"


if __name__ == "__main__":
    _selftest()
    main()
