"""backtest24_smc.py — Smart Money Concepts (SMC/ICT) diuji JUJUR pada data binary option OTC.

Protokol tetap: keputusan di T-5 detik candle A (hanya tick offset <= 55 s + candle yang
sudah tutup), entry = tick pertama candle B, hasil = close candle B vs entry.
Semua fitur SMC dihitung dari candle yang SUDAH TUTUP + candle berjalan yang terlihat
sampai T-5. Tidak ada state lookahead: swing di indeks j hanya dipakai bila j+p <= i-1.

Elemen SMC (parameter p, W dipilih di TRAIN saja):
  swing     pivot fraktal p candle kiri-kanan, terkonfirmasi p candle setelahnya
  BOS       close candle terakhir menembus swing high/low terkonfirmasi terakhir
  CHoCH     BOS yang arahnya berlawanan dengan BOS sebelumnya
  OB        candle berlawanan terakhir sebelum BOS; zona = low..high candle itu
  FVG       3 candle: l[k] > h[k-2] (bullish); zona = h[k-2]..l[k]
  sweep     ekor candle berjalan menembus swing terakhir lalu close kembali ke dalam
  P/D       posisi close dalam rentang W candle terakhir (premium > 0.65, discount < 0.35)
  HTF bias  arah candle M5 terakhir yang sudah tutup

Alur: tiap aturan dicari parameter terbaiknya di TRAIN (hari 0-5), lalu dilaporkan di
HOLDOUT (hari 6) dan LOCKBOX (hari 7) — dua segmen yang tidak dipakai memilih.
"""
from __future__ import annotations

import sys

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

import research_harness as H

PIVOT_GRID = (1, 2, 3)
WINDOW_GRID = (30, 60, 120)
MIN_TRAIN = 3000
INF = 1e18


def _rolling(a: np.ndarray, w: int, fn) -> np.ndarray:
    """fn atas jendela a[i-w:i] (candle yang sudah tutup). NaN bila belum cukup."""
    out = np.full(len(a), np.nan)
    if len(a) > w:
        sw = sliding_window_view(a, w)                   # sw[j] = a[j:j+w]
        out[w:] = fn(sw[:len(a) - w], axis=1)            # baris i memakai a[i-w:i]
    return out


def _prefix_last(flag: np.ndarray) -> np.ndarray:
    """indeks kemunculan terakhir flag (inklusif); -1 bila belum ada."""
    return np.maximum.accumulate(np.where(flag, np.arange(len(flag)), -1))


def smc(T: dict, p: int, W: int) -> dict:
    o, h, l, c = (T[k].astype(float) for k in ("o", "h", "l", "c"))
    o5, h5, l5, c5 = (T[k].astype(float) for k in ("o5", "h5", "l5", "c5"))
    n = len(c)
    ix = np.arange(n)

    # --- swing terkonfirmasi -------------------------------------------------
    padh = np.pad(h, (p, p), constant_values=-INF)
    padl = np.pad(l, (p, p), constant_values=INF)
    ph = np.abs(h - sliding_window_view(padh, 2 * p + 1).max(1)) < 1e-12
    pl = np.abs(l - sliding_window_view(padl, 2 * p + 1).min(1)) < 1e-12
    last_sh = np.full(n, -1, dtype=int)
    last_sl = np.full(n, -1, dtype=int)
    if n > p + 1:
        # geser p+1: swing di j hanya terlihat di i bila j + p <= i - 1
        last_sh[p + 1:] = _prefix_last(ph)[:-p - 1]
        last_sl[p + 1:] = _prefix_last(pl)[:-p - 1]
    hi_sh = np.where(last_sh >= 0, h[np.maximum(last_sh, 0)], INF)
    lo_sl = np.where(last_sl >= 0, l[np.maximum(last_sl, 0)], -INF)

    # --- BOS / CHoCH ---------------------------------------------------------
    cs = np.concatenate([[np.nan], c[:-1]])              # close candle terakhir yang tutup
    bos_up = (last_sh >= 0) & (cs > hi_sh)
    bos_dn = (last_sl >= 0) & (cs < lo_sl)
    ev = np.where(bos_up, 1, np.where(bos_dn, -1, 0))
    last_ev = _prefix_last(ev != 0)
    prev_ev = np.where(last_ev > 0, ev[np.maximum(last_ev - 1, 0)], 0)
    choch_up = bos_up & (prev_ev == -1)
    choch_dn = bos_dn & (prev_ev == 1)

    # --- Order Block ---------------------------------------------------------
    bearish = c < o
    bullish = c > o
    last_bear = _prefix_last(bearish)
    last_bull = _prefix_last(bullish)
    ub = _prefix_last(bos_up)
    db = _prefix_last(bos_dn)
    ob_u = np.where(ub > 0, last_bear[np.maximum(ub - 1, 0)], -1)   # OB bullish
    ob_d = np.where(db > 0, last_bull[np.maximum(db - 1, 0)], -1)   # OB bearish
    fresh_u = (ub >= 0) & (ix - ub <= W)
    fresh_d = (db >= 0) & (ix - db <= W)
    ob_u_hi = np.where(ob_u >= 0, h[np.maximum(ob_u, 0)], np.nan)
    ob_d_lo = np.where(ob_d >= 0, l[np.maximum(ob_d, 0)], np.nan)

    # --- FVG -----------------------------------------------------------------
    fvg_u = np.zeros(n, bool)
    fvg_d = np.zeros(n, bool)
    fvg_u[2:] = l[2:] > h[:-2]
    fvg_d[2:] = h[2:] < l[:-2]
    lu = _prefix_last(fvg_u)
    ld = _prefix_last(fvg_d)
    fresh_fu = (lu >= 0) & (ix - lu <= W)
    fresh_fd = (ld >= 0) & (ix - ld <= W)
    fvu_hi = np.where(lu >= 0, l[np.maximum(lu, 0)], np.nan)          # batas atas zona bullish
    fvd_lo = np.where(ld >= 0, h[np.maximum(ld, 0)], np.nan)          # batas bawah zona bearish

    # --- premium / discount --------------------------------------------------
    rmin = _rolling(l, W, np.min)
    rmax = _rolling(h, W, np.max)
    span = np.maximum(rmax - rmin, 1e-12)
    pos = (cs - rmin) / span

    # --- ATR (candle tutup) --------------------------------------------------
    atr = _rolling(h - l, 14, np.mean)

    # --- EQH / EQL -----------------------------------------------------------
    pl_pref = _prefix_last(ph)
    sl_pref = _prefix_last(pl)
    prev_sh = np.full(n, -1, dtype=int)
    has2 = (last_sh >= 1)
    prev_sh[has2] = pl_pref[np.maximum(last_sh[has2] - 1, 0)]
    prev_sl = np.full(n, -1, dtype=int)
    has2l = (last_sl >= 1)
    prev_sl[has2l] = sl_pref[np.maximum(last_sl[has2l] - 1, 0)]
    eqh = (prev_sh >= 0) & (np.abs(h[np.maximum(last_sh, 0)] - h[np.maximum(prev_sh, 0)]) < 0.25 * atr)
    eql = (prev_sl >= 0) & (np.abs(l[np.maximum(last_sl, 0)] - l[np.maximum(prev_sl, 0)]) < 0.25 * atr)

    # --- HTF bias M5 (blok 5 menit terakhir yang sudah tutup) ----------------
    blk = T["t"] // 300
    ub_blk, bstart = np.unique(blk, return_index=True)
    bend = np.append(bstart[1:], n)
    k = np.searchsorted(ub_blk, blk)
    kk = np.maximum(k - 1, 0)
    contig = (k >= 1) & (ub_blk[kk] == blk - 1)
    m5 = np.where(contig, np.sign(c[bend[kk] - 1] - o[bstart[kk]]), np.nan)

    return dict(last_sh=last_sh, last_sl=last_sl, hi_sh=hi_sh, lo_sl=lo_sl,
                bos_up=bos_up, bos_dn=bos_dn, choch_up=choch_up, choch_dn=choch_dn,
                fresh_u=fresh_u, fresh_d=fresh_d, ob_u_hi=ob_u_hi, ob_d_lo=ob_d_lo,
                fresh_fu=fresh_fu, fresh_fd=fresh_fd, fvu_hi=fvu_hi, fvd_lo=fvd_lo,
                pos=pos, atr=atr, eqh=eqh, eql=eql, m5=m5,
                recent_sh=(last_sh >= 0) & (ix - last_sh <= W),
                recent_sl=(last_sl >= 0) & (ix - last_sl <= W),
                h5=h5, l5=l5, c5=c5)


def _dir(up: np.ndarray, dn: np.ndarray) -> np.ndarray:
    return np.where(up, 1.0, np.where(dn, -1.0, 0.0))


def rules(S: dict) -> dict[str, np.ndarray]:
    """Semua aturan SMC -> +1 CALL / -1 PUT / 0 tidak ada sinyal."""
    h5, l5, c5 = S["h5"], S["l5"], S["c5"]
    sw_up = S["recent_sl"] & (l5 < S["lo_sl"]) & (c5 > S["lo_sl"])
    sw_dn = S["recent_sh"] & (h5 > S["hi_sh"]) & (c5 < S["hi_sh"])
    ob_up = S["fresh_u"] & (l5 <= S["ob_u_hi"]) & (c5 > S["ob_u_hi"])
    ob_dn = S["fresh_d"] & (h5 >= S["ob_d_lo"]) & (c5 < S["ob_d_lo"])
    fvg_up = S["fresh_fu"] & (l5 <= S["fvu_hi"]) & (c5 > S["fvu_hi"])
    fvg_dn = S["fresh_fd"] & (h5 >= S["fvd_lo"]) & (c5 < S["fvd_lo"])
    disc = S["pos"] < 0.35
    prem = S["pos"] > 0.65
    htf_up = S["m5"] > 0
    htf_dn = S["m5"] < 0
    bosu, bosd = S["bos_up"], S["bos_dn"]
    chu, chd = S["choch_up"], S["choch_dn"]

    def d(up, dn):
        return _dir(up, dn)

    return {
        "R1_bos_follow": d(bosu, bosd),
        "R2_choch": d(chu, chd),
        "R3_ob_retest": d(ob_up, ob_dn),
        "R4_fvg_fill": d(fvg_up, fvg_dn),
        "R5_sweep": d(sw_up, sw_dn),
        "R6_prem_disc": d(disc, prem),
        "R7_sweep_htf": d(sw_up & htf_up, sw_dn & htf_dn),
        "R8_ob_htf": d(ob_up & htf_up, ob_dn & htf_dn),
        "R9_sweep_pd": d(sw_up & disc, sw_dn & prem),
        "R10_choch_fvg": d(chu & fvg_up, chd & fvg_dn),
        "R11_bos_htf": d(bosu & htf_up, bosd & htf_dn),
        "R12_eql_sweep": d(sw_up & S["eql"], sw_dn & S["eqh"]),
        "R13_fvg_htf": d(fvg_up & htf_up, fvg_dn & htf_dn),
        "R14_all_agree": d(sw_up & ob_up & htf_up & disc, sw_dn & ob_dn & htf_dn & prem),
        "R15_sweep_ob": d(sw_up & ob_up, sw_dn & ob_dn),
    }


def tally(dirs: np.ndarray, T: dict) -> tuple[int, int, int]:
    m = T["ok"] & (dirs != 0)
    y = T["y"][m].astype(float)
    p = dirs[m]
    return int((y == p).sum()), int((y == -p).sum()), int((y == 0).sum())


def main() -> None:
    tabs = H.load_tables()
    d0 = H._day_index(tabs)
    print(f"simbol={len(tabs)}", flush=True)
    grid = [(p, W) for p in PIVOT_GRID for W in WINDOW_GRID]
    store: dict[tuple[str, int, int], dict[str, list[int]]] = {}
    for p, W in grid:
        for T in tabs.values():
            S = smc(T, p, W)
            day = T["t"] // H.DAY - d0
            segs = {"train": day < H.HOLDOUT_FROM_DAY,
                    "holdout": (day >= H.HOLDOUT_FROM_DAY) & (day < H.LOCKBOX_FROM_DAY),
                    "lockbox": day >= H.LOCKBOX_FROM_DAY}
            for rname, dirs in rules(S).items():
                slot = store.setdefault((rname, p, W), {s: [0, 0, 0] for s in segs})
                for s, msk in segs.items():
                    w, lo, dr = tally(np.where(msk, dirs, 0.0), T)
                    slot[s][0] += w; slot[s][1] += lo; slot[s][2] += dr
        print(f"param p={p} W={W} selesai", flush=True)

    names = sorted({k[0] for k in store})
    best: dict[str, tuple] = {}
    for name in names:
        for p, W in grid:
            tr = store[(name, p, W)]["train"]
            ntr = sum(tr)
            if ntr < MIN_TRAIN:
                continue
            wr = tr[0] / ntr
            if name not in best or wr > best[name][0]:
                best[name] = (wr, p, W)

    print(f"\n{'aturan':16} {'p':>2} {'W':>4} | {'TRAIN':>7} {'n':>7} | {'HOLD':>7} {'n':>7} {'lb95':>6} | "
          f"{'LOCK':>7} {'n':>7} {'lb95':>6} {'d(pt)':>6}")
    rows = []
    for name in names:
        if name not in best:
            continue
        _, p, W = best[name]
        slot = store[(name, p, W)]
        tr, ho, lb = slot["train"], slot["holdout"], slot["lockbox"]
        ntr, nho, nlb = sum(tr), sum(ho), sum(lb)
        if nho == 0 or nlb == 0:
            continue
        dlb = (lb[0] - lb[1]) / nlb * 100
        rows.append((name, p, W, tr[0] / ntr * 100, ntr, ho[0] / nho * 100, nho,
                     H.wilson_low(ho[0], nho), lb[0] / nlb * 100, nlb,
                     H.wilson_low(lb[0], nlb), dlb))
    rows.sort(key=lambda r: -r[5])
    for r in rows:
        print(f"{r[0]:16} {r[1]:2d} {r[2]:4d} | {r[3]:6.2f}% {r[4]:7d} | {r[5]:6.2f}% {r[6]:7d} {r[7]:6.2f} | "
              f"{r[8]:6.2f}% {r[9]:7d} {r[10]:6.2f} {r[11]:+6.2f}")
    if rows:
        ho = np.array([r[5] for r in rows])
        lb = np.array([r[8] for r in rows])
        print(f"\n{len(rows)} aturan (param terbaik per TRAIN): HOLD median={np.median(ho):.2f}% max={ho.max():.2f}% "
              f"| LOCK median={np.median(lb):.2f}% max={lb.max():.2f}%")
        print(f"lb95 holdout > 50: {sum(1 for r in rows if r[7] > 50)}/{len(rows)}; "
              f"lb95 lockbox > 50: {sum(1 for r in rows if r[10] > 50)}/{len(rows)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        # tidak ada lookahead: swing di j hanya dipakai bila j + p <= i - 1
        t = np.arange(0, 60 * 60, 60.0)
        rng = np.random.default_rng(3)
        px = np.cumsum(rng.normal(0, 1, len(t))) + 100
        T = dict(t=t.astype(np.int64), o=px.copy(), h=px + 1, l=px - 1, c=px.copy(),
                 o5=px.copy(), h5=px + 1, l5=px - 1, c5=px.copy(), n5=np.full(len(t), 5),
                 n=np.full(len(t), 10), entry=np.roll(px, -1), closeb=np.roll(px, -1),
                 y=np.zeros(len(t), np.int8), ok=np.ones(len(t), bool))
        S = smc(T, 2, 60)
        assert (S["last_sh"][:3] == -1).all(), "swing dipakai sebelum terkonfirmasi"
        for i in range(10, 40):
            if S["last_sh"][i] >= 0:
                assert S["last_sh"][i] + 2 <= i - 1, "lookahead swing"
        R = rules(S)
        assert all(len(v) == len(t) for v in R.values()), "panjang aturan tidak sama"
        assert set(np.unique(R["R1_bos_follow"])) <= {-1.0, 0.0, 1.0}
        print("selftest OK: swing tanpa lookahead, 15 aturan sepanjang data")
    else:
        main()
