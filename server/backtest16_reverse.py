"""backtest16_reverse.py — reverse-engineering generator harga OTC pada level tick.

Pertanyaan: apakah ada struktur di generator broker yang bisa dipakai pada T-5?
Semua uji memakai research_harness (keputusan T-5, entry = open B, hasil = close B).

Keluarga uji (masing-masing hipotesis mekanis tentang generator):
  A. Pinning/rounding: harga di kisi digit tertentu cenderung kembali?
  B. Close-B vs entry: drift sistematis per detik-ke-n / per simbol / per jam.
  C. Mean-reversion intra-candle: posisi c5 dalam range T-5 -> arah B.
  D. Momentum tick terakhir (dorongan 5-15 s sebelum keputusan).
  E. Autokorelasi return tick lag-1 (generator AR?) -> jejak di level menit.
  F. Seasonality detik-dalam-menit: apakah tick awal menit punya bias?
  G. Rezim volatilitas tinggi x z-score (strategi aktif) diukur ulang di T-5.
  H. Ensemble suara mayoritas dari aturan yang lolos train.

Seleksi dari TRAIN (hari 0-4); angka yang dilaporkan dari HOLDOUT.
"""
from __future__ import annotations

import numpy as np

from research_harness import evaluate, load_tables, load_ticks, wilson_low


def roll_mean(x: np.ndarray, p: int) -> np.ndarray:
    c = np.cumsum(np.insert(np.nan_to_num(x), 0, 0.0))
    out = np.full(len(x), np.nan)
    out[p - 1:] = (c[p:] - c[:-p]) / p
    return out


def shift(x: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[k:] = x[:-k]
    return out


def feats(T: dict) -> dict:
    """Fitur yang terlihat pada T-5 candle i. Data penuh hanya dari menit < i."""
    if "_f" in T:
        return T["_f"]
    c_prev = shift(T["c"])                     # close menit i-1 (tertutup)
    rng_prev = shift(T["h"] - T["l"])
    atr = roll_mean(rng_prev, 14)
    # z-score memakai close penuh i-1..i-20 plus harga T-5 sekarang
    sma = (roll_mean(c_prev, 19) * 19 + T["c5"]) / 20
    dev = roll_mean(np.abs(np.diff(T["c"], prepend=T["c"][0])), 20)
    dev = shift(dev)
    r5 = T["h5"] - T["l5"]
    f = {
        "z": np.where(dev > 0, (T["c5"] - sma) / dev, 0.0),
        "pos": np.where(r5 > 0, (T["c5"] - T["l5"]) / np.where(r5 > 0, r5, 1) * 2 - 1, 0.0),
        "body5": np.where(atr > 0, (T["c5"] - T["o5"]) / np.where(atr > 0, atr, 1), 0.0),
        "gap": np.where(atr > 0, (T["o5"] - c_prev) / np.where(atr > 0, atr, 1), 0.0),
        "volhi": r5 > np.nan_to_num(atr),
        "prev_dir": np.sign(T["c"] - T["o"]),
        "hour": (T["t"] // 3600) % 24,
    }
    f["prev_dir"] = shift(f["prev_dir"])
    T["_f"] = f
    return f


def sgn(x: np.ndarray) -> np.ndarray:
    return np.sign(np.nan_to_num(x))


def family_rules() -> dict:
    R = {}
    for th in (1.5, 2.0, 2.5, 3.0):
        R[f"z{th} revert"] = lambda T, th=th: np.where(feats(T)["z"] < -th, 1, np.where(feats(T)["z"] > th, -1, 0))
        R[f"z{th} revert volHI"] = lambda T, th=th: np.where(feats(T)["volhi"], R[f"z{th} revert"](T), 0)
        R[f"z{th} follow"] = lambda T, th=th: -R[f"z{th} revert"](T)
    for th in (0.6, 0.8, 0.95):
        R[f"pos{th} revert"] = lambda T, th=th: np.where(feats(T)["pos"] < -th, 1, np.where(feats(T)["pos"] > th, -1, 0))
        R[f"pos{th} follow"] = lambda T, th=th: -R[f"pos{th} revert"](T)
    for th in (0.5, 1.0, 1.5):
        R[f"body{th} revert"] = lambda T, th=th: np.where(feats(T)["body5"] < -th, 1, np.where(feats(T)["body5"] > th, -1, 0))
        R[f"body{th} follow"] = lambda T, th=th: -R[f"body{th} revert"](T)
        R[f"gap{th} fill"] = lambda T, th=th: np.where(feats(T)["gap"] < -th, 1, np.where(feats(T)["gap"] > th, -1, 0))
    R["prev candle follow"] = lambda T: feats(T)["prev_dir"]
    R["prev candle revert"] = lambda T: -feats(T)["prev_dir"]
    R["kontrol always CALL"] = lambda T: np.ones(len(T["t"]))
    return R


def tick_autocorr(tabs: dict, sample: int = 30) -> None:
    """E: autokorelasi return tick lag-1..3 — generator AR akan terlihat di sini."""
    print("\n== E. autokorelasi return tick (lag1, lag2, lag3) ==")
    acs = []
    for s in list(tabs)[:sample]:
        t, p = load_ticks(s)
        r = np.diff(p)
        r = r[r != 0]
        if len(r) < 1000:
            continue
        r = (r - r.mean()) / (r.std() or 1)
        ac = [float(np.mean(r[:-k] * r[k:])) for k in (1, 2, 3)]
        acs.append(ac)
        print(f"  {s:16} {ac[0]:+.4f} {ac[1]:+.4f} {ac[2]:+.4f}  n={len(r)}")
    a = np.array(acs)
    print(f"  RATA-RATA         {a[:, 0].mean():+.4f} {a[:, 1].mean():+.4f} {a[:, 2].mean():+.4f}")


def second_bias(tabs: dict, sample: int = 40) -> None:
    """F: rata-rata tanda return per detik-dalam-menit; bias sistematis = jejak generator."""
    print("\n== F. P(tick naik) per detik-dalam-menit (deviasi dari 50%) ==")
    up = np.zeros(60); n = np.zeros(60)
    for s in list(tabs)[:sample]:
        t, p = load_ticks(s)
        r = np.sign(np.diff(p)); sec = (t[1:] % 60).astype(int)
        m = r != 0
        np.add.at(up, sec[m], (r[m] > 0)); np.add.at(n, sec[m], 1)
    dev = (up / np.maximum(n, 1) - 0.5) * 100
    worst = np.argsort(-np.abs(dev))[:6]
    print("  terbesar:", ", ".join(f"s{k}:{dev[k]:+.2f}pt(n={int(n[k])})" for k in worst))


def grid_pin(tabs: dict, sample: int = 40) -> None:
    """A: digit terakhir harga entry -> arah B. Generator berbasis kisi akan bias di sini."""
    print("\n== A. digit terakhir entry -> P(B naik) ==")
    rows = []
    for s in list(tabs)[:sample]:
        T = tabs[s]
        ok = T["ok"] & (T["y"] != 0)
        e = T["entry"][ok]
        txt = [f"{v:.10g}" for v in e]
        last = np.array([int(x[-1]) if x[-1].isdigit() else -1 for x in txt])
        rows.append((last, T["y"][ok] > 0))
    last = np.concatenate([r[0] for r in rows]); up = np.concatenate([r[1] for r in rows])
    for d in range(10):
        m = last == d
        if m.sum() > 500:
            print(f"  digit {d}: {up[m].mean() * 100:6.2f}% n={m.sum()} lb={wilson_low(up[m].sum(), m.sum()):.2f}")


def main() -> None:
    tabs = load_tables()
    print(f"symbols={len(tabs)} minutes={sum(len(T['t']) for T in tabs.values()):,}")
    print("\n== B/C/D/G. keluarga aturan T-5 (train hari 0-4, HOLDOUT hari 5+) ==")
    results = [evaluate(name, rule, tabs) for name, rule in family_rules().items()]
    tick_autocorr(tabs)
    second_bias(tabs)
    grid_pin(tabs)

    # H. ensemble: hanya aturan yang train >= 50.5% dan n train >= 2000 (dipilih di TRAIN)
    picked = [r["name"] for r in results if r["train"]["wr"] >= 50.5 and r["train"]["n"] >= 2000
              and not r["name"].startswith("kontrol")]
    print("\n== H. ensemble dari pilihan TRAIN:", picked)
    R = family_rules()
    if picked:
        def vote(T):
            s = sum(np.asarray(R[k](T), dtype=float) for k in picked)
            return np.sign(s) * (np.abs(s) >= max(1, len(picked) // 2))
        evaluate("ENSEMBLE majority", vote, tabs)
    best = sorted(results, key=lambda r: -r["holdout"]["lb"])[:5]
    print("\nTOP-5 menurut lb95 HOLDOUT:")
    for r in best:
        print(f"  {r['name']:30} {r['holdout']['wr']:.2f}% lb={r['holdout']['lb']:.2f} n={r['holdout']['n']}")


if __name__ == "__main__":
    main()
