"""backtest26_smc_canon.py — SMC KANONIK: memperbaiki penyimpangan implementasi bt24/bt25.

Riset (workflow smc-binary-spec, sumber: LuxAlgo, joshyattridge/smart-money-concepts,
smc-mcp, ICT/DT Academy, LiquidityScan, KVignesh MT5 EA, auto-ict) menemukan bt24/bt25
menyimpang dari definisi kanonik ICT/SMC. Skrip ini memakai definisi kanonik lalu menguji
ulang pada data yang sama.

Perbaikan:
  CHoCH     close menembus HIGHER LOW terakhir (swing low yang meluncurkan high terakhir),
            bukan sekadar BOS berlawanan arah BOS sebelumnya
  swing     bertingkat: STH (fraktal p) -> ITH (STH diapit STH lebih rendah)
            -> LTH (ITH diapit ITH lebih rendah); semuanya menunggu konfirmasi p candle
  OB        zona BODY (open..close) = konvensi paling umum; diambil dari candle berlawanan
            terakhir sebelum candle DISPLACEMENT (bukan sebelum BOS apa pun)
  displacement  body >= m x rata-rata body(14) DAN body >= 0.8 x range (wick < 20%)
  FVG       wajib candle tengah searah (close>open untuk bullish) + gap >= g x ATR
  sweep     reclaim diizinkan dalam 1..3 candle, bukan hanya candle sweep
  EQH/EQL   toleransi 0.1 x ATR (default LuxAlgo)
  OTE       retracement 62%..79% dari leg terakhir
  MTF       bias H1 (candle 1 jam terakhir yang tutup) sebagai penguasa arah
  BOS cepat varian rolling-max N candle (profittown/Quant-Edge), tanpa delay pivot

Protokol tetap: keputusan T-5, entry tick pertama candle B, hasil = close B vs entry.
Parameter dipilih di TRAIN (hari 0-5), dilaporkan di HOLDOUT (hari 6) dan LOCKBOX (hari 7).
"""
from __future__ import annotations

import sys

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

import research_harness as H
from backtest24_smc import INF, _dir, _prefix_last, _rolling

P_GRID = (1, 2)
DISP_GRID = (1.5, 2.0)
ROLL = 20
EQ_TOL = 0.10          # x ATR (LuxAlgo default)
FVG_MIN_ATR = 0.10     # KVignesh adaptive floor
SWEEP_BARS = 3
OTE_LO, OTE_HI = 0.618, 0.79
W = 60                 # umur zona (candle) sebelum kedaluwarsa
MIN_TRAIN = 3000


def _fractal(h: np.ndarray, l: np.ndarray, p: int) -> tuple[np.ndarray, np.ndarray]:
    """Fraktal p candle kiri-kanan. Titik di j baru terkonfirmasi di j+p."""
    n = len(h)
    ph = np.zeros(n, bool)
    pl = np.zeros(n, bool)
    if n > 2 * p:
        wh = sliding_window_view(np.pad(h, (p, p), constant_values=-INF), 2 * p + 1).max(1)
        wl = sliding_window_view(np.pad(l, (p, p), constant_values=INF), 2 * p + 1).min(1)
        ph[p:n - p] = h[p:n - p] >= wh[p:n - p]
        pl[p:n - p] = l[p:n - p] <= wl[p:n - p]
    return ph, pl


def _confirmed(flag: np.ndarray, price: np.ndarray, p: int) -> tuple[np.ndarray, np.ndarray]:
    """flag[i] = titik swing di i. Kembalikan (indeks, harga) swing terakhir yang SUDAH
    terkonfirmasi pada baris i, yaitu j + p <= i."""
    n = len(flag)
    last = np.full(n, -1, dtype=int)
    if n > p:
        last[p:] = _prefix_last(flag)[:-p]
    lvl = np.where(last >= 0, price[np.maximum(last, 0)], np.nan)
    return last, lvl


def _tier(flag: np.ndarray, price: np.ndarray, p: int, higher: bool) -> tuple[np.ndarray, np.ndarray]:
    """Swing tingkat berikutnya: flag yang diapit dua flag lain dengan harga lebih rendah
    (higher=True) atau lebih tinggi (higher=False). Terkonfirmasi p candle setelah flag kanan."""
    n = len(flag)
    out = np.full(n, -1, dtype=int)
    idx = np.flatnonzero(flag)
    if len(idx) < 3:
        return out, np.full(n, np.nan)
    pv = price[idx]
    if higher:
        mid = (pv[1:-1] > pv[:-2]) & (pv[1:-1] > pv[2:])
    else:
        mid = (pv[1:-1] < pv[:-2]) & (pv[1:-1] < pv[2:])
    sel = np.flatnonzero(mid)
    if len(sel) == 0:
        return out, np.full(n, np.nan)
    confirm = idx[sel + 1] + p                    # bar saat tingkat ini mulai terlihat
    keep = confirm < n
    sel, confirm = sel[keep], confirm[keep]
    if len(sel) == 0:
        return out, np.full(n, np.nan)
    for i in range(n):
        k = np.searchsorted(confirm, i, side="right") - 1
        if k >= 0:
            out[i] = idx[sel[k]]
    lvl = np.where(out >= 0, price[np.maximum(out, 0)], np.nan)
    return out, lvl


def smc_canon(T: dict, p: int, disp: float) -> dict[str, np.ndarray]:
    o, h, l, c = (T[k].astype(float) for k in ("o", "h", "l", "c"))
    o5, h5, l5, c5 = (T[k].astype(float) for k in ("o5", "h5", "l5", "c5"))
    n = len(c)
    ix = np.arange(n)

    # --- struktur bertingkat -------------------------------------------------
    ph, pl = _fractal(h, l, p)
    sh_i, sh_p = _confirmed(ph, h, p)             # STH terkonfirmasi
    sl_i, sl_p = _confirmed(pl, l, p)             # STL terkonfirmasi
    sth_flag = np.zeros(n, bool)
    sth_flag[sh_i[sh_i >= 0]] = True
    stl_flag = np.zeros(n, bool)
    stl_flag[sl_i[sl_i >= 0]] = True
    ith_i, ith_p = _tier(sth_flag, h, p, higher=True)
    itl_i, itl_p = _tier(stl_flag, l, p, higher=False)
    lth_flag = np.zeros(n, bool)
    lth_flag[ith_i[ith_i >= 0]] = True
    ltl_flag = np.zeros(n, bool)
    ltl_flag[itl_i[itl_i >= 0]] = True
    lth_i, lth_p = _tier(lth_flag, h, p, higher=True)
    ltl_i, ltl_p = _tier(ltl_flag, l, p, higher=False)

    # --- ATR, body, displacement --------------------------------------------
    rng = h - l
    atr = _rolling(rng, 14, np.mean)
    avg_body = _rolling(np.abs(c - o), 14, np.mean)
    body = np.abs(c - o)
    ok_atr = np.isfinite(atr) & (atr > 0)
    ok_body = np.isfinite(avg_body) & (avg_body > 0)
    dsp = ok_atr & ok_body & (body >= disp * avg_body) & (body >= 0.8 * rng)

    # --- close candle terakhir yang tutup ------------------------------------
    cs = np.concatenate([[np.nan], c[:-1]])
    dsp_prev = np.concatenate([[False], dsp[:-1]])

    # --- BOS ---------------------------------------------------------------
    bos_up = (sh_i >= 0) & (cs > sh_p)
    bos_dn = (sl_i >= 0) & (cs < sl_p)
    rmax = _rolling(h, ROLL, np.max)
    rmin = _rolling(l, ROLL, np.min)
    bos_up_r = np.isfinite(rmax) & (cs > rmax)
    bos_dn_r = np.isfinite(rmin) & (cs < rmin)

    # --- CHoCH kanonik ------------------------------------------------------
    # Higher low = STL yang lebih tinggi dari STL sebelumnya. CHoCH bearish = close
    # menembus higher low terakhir. Cermin: lower high untuk CHoCH bullish.
    prev_sl = np.full(n, -1, dtype=int)
    v_sl = sl_i >= 0
    if v_sl.any():
        pref = _prefix_last(np.isin(ix, sl_i[v_sl]))
        prev_sl[v_sl] = pref[np.maximum(sl_i[v_sl] - 1, 0)]
        prev_sl[v_sl] = np.where(np.isin(prev_sl[v_sl], sl_i[v_sl]), prev_sl[v_sl], -1)
    hl_flag = np.zeros(n, bool)
    vh = (sl_i >= 0) & (prev_sl >= 0)
    hl_flag[sl_i[vh]] = (sl_p[vh] > l[np.maximum(prev_sl[vh], 0)])
    hl_seq = np.maximum.accumulate(np.where(hl_flag, ix, -1))
    choch_dn_c = (hl_seq >= 0) & (cs < l[np.maximum(hl_seq, 0)])

    prev_sh = np.full(n, -1, dtype=int)
    v_sh = sh_i >= 0
    if v_sh.any():
        prefh = _prefix_last(np.isin(ix, sh_i[v_sh]))
        prev_sh[v_sh] = prefh[np.maximum(sh_i[v_sh] - 1, 0)]
        prev_sh[v_sh] = np.where(np.isin(prev_sh[v_sh], sh_i[v_sh]), prev_sh[v_sh], -1)
    lh_flag = np.zeros(n, bool)
    vhh = (sh_i >= 0) & (prev_sh >= 0)
    lh_flag[sh_i[vhh]] = (sh_p[vhh] < h[np.maximum(prev_sh[vhh], 0)])
    lh_seq = np.maximum.accumulate(np.where(lh_flag, ix, -1))
    choch_up_c = (lh_seq >= 0) & (cs > h[np.maximum(lh_seq, 0)])

    # --- Order Block sebelum displacement -----------------------------------
    ubd = _prefix_last(bos_up & dsp_prev)
    dbd = _prefix_last(bos_dn & dsp_prev)
    bear, bull = c < o, c > o
    ob_u_i = np.where(ubd > 0, _prefix_last(bear)[np.maximum(ubd - 1, 0)], -1)
    ob_d_i = np.where(dbd > 0, _prefix_last(bull)[np.maximum(dbd - 1, 0)], -1)
    fresh_u = (ubd >= 0) & (ix - ubd <= W) & (ob_u_i >= 0)
    fresh_d = (dbd >= 0) & (ix - dbd <= W) & (ob_d_i >= 0)
    ob_u_hi = np.where(ob_u_i >= 0, np.maximum(o[np.maximum(ob_u_i, 0)], c[np.maximum(ob_u_i, 0)]), np.nan)
    ob_u_lo = np.where(ob_u_i >= 0, np.minimum(o[np.maximum(ob_u_i, 0)], c[np.maximum(ob_u_i, 0)]), np.nan)
    ob_d_hi = np.where(ob_d_i >= 0, np.maximum(o[np.maximum(ob_d_i, 0)], c[np.maximum(ob_d_i, 0)]), np.nan)
    ob_d_lo = np.where(ob_d_i >= 0, np.minimum(o[np.maximum(ob_d_i, 0)], c[np.maximum(ob_d_i, 0)]), np.nan)

    # --- FVG kanonik ---------------------------------------------------------
    gap_u = l[2:] - h[:-2]
    gap_d = l[:-2] - h[2:]
    thr = FVG_MIN_ATR * np.nan_to_num(atr[2:], nan=1e18)
    fvg_u = np.zeros(n, bool)
    fvg_d = np.zeros(n, bool)
    fvg_u[2:] = (gap_u > 0) & (c[1:-1] > o[1:-1]) & (gap_u >= thr)
    fvg_d[2:] = (gap_d > 0) & (c[1:-1] < o[1:-1]) & (gap_d >= thr)
    lu = _prefix_last(fvg_u)
    ld = _prefix_last(fvg_d)
    fu = (lu >= 0) & (ix - lu <= W)
    fd = (ld >= 0) & (ix - ld <= W)
    fvu_hi = np.where(lu >= 0, l[np.maximum(lu, 0)], np.nan)
    fvd_lo = np.where(ld >= 0, h[np.maximum(ld, 0)], np.nan)

    # --- sweep dengan reclaim 1..S candle ------------------------------------
    def sweep(side: str) -> np.ndarray:
        out = np.zeros(n, bool)
        lvl = sl_p if side == "low" else sh_p
        for k in range(SWEEP_BARS):
            if k:
                lv = np.concatenate([np.full(k, np.nan), lvl[:-k]])
                p5 = np.concatenate([np.full(k, False), (l5 < lvl)[:-k]]) if side == "low" \
                    else np.concatenate([np.full(k, False), (h5 > lvl)[:-k]])
                b5 = np.concatenate([np.full(k, False), (c5 > lvl)[:-k]]) if side == "low" \
                    else np.concatenate([np.full(k, False), (c5 < lvl)[:-k]])
            else:
                lv = lvl
                p5 = (l5 < lvl) if side == "low" else (h5 > lvl)
                b5 = (c5 > lvl) if side == "low" else (c5 < lvl)
            out |= np.isfinite(lv) & p5 & b5
        return out

    sw_up = sweep("low")
    sw_dn = sweep("high")

    # --- EQH / EQL toleransi 0.1 ATR ----------------------------------------
    eqh = np.zeros(n, bool)
    eql = np.zeros(n, bool)
    v_h = (sh_i >= 0) & (prev_sh >= 0)
    eqh[v_h] = np.abs(h[np.maximum(sh_i[v_h], 0)] - h[np.maximum(prev_sh[v_h], 0)]) < EQ_TOL * atr[v_h]
    v_l = (sl_i >= 0) & (prev_sl >= 0)
    eql[v_l] = np.abs(l[np.maximum(sl_i[v_l], 0)] - l[np.maximum(prev_sl[v_l], 0)]) < EQ_TOL * atr[v_l]

    # --- OTE 62%-79% ---------------------------------------------------------
    span = sh_p - sl_p
    okleg = np.isfinite(span) & (span > 0)
    den = np.where(okleg, span, 1.0)
    retr_from_hi = np.where(okleg, (sh_p - c5) / den, np.nan)
    retr_from_lo = np.where(okleg, (c5 - sl_p) / den, np.nan)
    ote_long = okleg & (retr_from_hi >= OTE_LO) & (retr_from_hi <= OTE_HI)
    ote_short = okleg & (retr_from_lo >= OTE_LO) & (retr_from_lo <= OTE_HI)

    # --- bias H1 -------------------------------------------------------------
    blk = T["t"] // 3600
    ub, start = np.unique(blk, return_index=True)
    end = np.append(start[1:], n)
    k = np.searchsorted(ub, blk)
    kk = np.maximum(k - 1, 0)
    contig = (k >= 1) & (ub[kk] == blk - 1)
    h1 = np.where(contig, np.sign(c[end[kk] - 1] - o[start[kk]]), np.nan)
    h1_up, h1_dn = h1 > 0, h1 < 0

    def d(up, dn):
        return _dir(up, dn)

    ob_up_touch = fresh_u & (l5 <= ob_u_hi) & (c5 > ob_u_lo)
    ob_dn_touch = fresh_d & (h5 >= ob_d_lo) & (c5 < ob_d_hi)
    fvg_up_touch = fu & (l5 <= fvu_hi) & (c5 > fvu_hi)
    fvg_dn_touch = fd & (h5 >= fvd_lo) & (c5 < fvd_lo)
    dsp2 = dsp & dsp_prev

    return {
        "K1_choch_canon": d(choch_up_c, choch_dn_c),
        "K2_choch_disp": d(choch_up_c & dsp2, choch_dn_c & dsp2),
        "K3_bos_pivot": d(bos_up, bos_dn),
        "K4_bos_rollmax": d(bos_up_r, bos_dn_r),
        "K5_bos_ith": d((ith_i >= 0) & (cs > ith_p), (itl_i >= 0) & (cs < itl_p)),
        "K6_bos_lth": d((lth_i >= 0) & (cs > lth_p), (ltl_i >= 0) & (cs < ltl_p)),
        "K7_ob_body": d(ob_up_touch, ob_dn_touch),
        "K8_fvg_canon": d(fvg_up_touch, fvg_dn_touch),
        "K9_sweep_reclaim3": d(sw_up, sw_dn),
        "K10_ote": d(ote_long, ote_short),
        "K11_eqh_01atr": d(eql & sw_up, eqh & sw_dn),
        "K12_htf_h1": d(h1_up, h1_dn),
        "K13_seq_sweep_choch": d(sw_up & choch_up_c & dsp2, sw_dn & choch_dn_c & dsp2),
        "K14_seq_htf_sweep": d(h1_up & sw_up, h1_dn & sw_dn),
        "K15_seq_htf_choch": d(h1_up & choch_up_c, h1_dn & choch_dn_c),
        "K16_seq_htf_ob_fvg": d(h1_up & (ob_up_touch | fvg_up_touch), h1_dn & (ob_dn_touch | fvg_dn_touch)),
        "K17_seq_full": d(h1_up & sw_up & choch_up_c & (ob_up_touch | fvg_up_touch),
                          h1_dn & sw_dn & choch_dn_c & (ob_dn_touch | fvg_dn_touch)),
        "K18_seq_htf_ote": d(h1_up & ote_long, h1_dn & ote_short),
    }


def main() -> None:
    tabs = H.load_tables()
    d0 = H._day_index(tabs)
    print(f"simbol={len(tabs)}", flush=True)
    grid = [(p, dv) for p in P_GRID for dv in DISP_GRID]
    store: dict[tuple[str, int, float], dict[str, list[int]]] = {}
    for p, dv in grid:
        for T in tabs.values():
            day = T["t"] // H.DAY - d0
            segs = {"train": day < H.HOLDOUT_FROM_DAY,
                    "holdout": (day >= H.HOLDOUT_FROM_DAY) & (day < H.LOCKBOX_FROM_DAY),
                    "lockbox": day >= H.LOCKBOX_FROM_DAY}
            for rname, dirs in smc_canon(T, p, dv).items():
                slot = store.setdefault((rname, p, dv), {s: [0, 0, 0] for s in segs})
                for s, msk in segs.items():
                    m = msk & T["ok"] & (dirs != 0)
                    y = T["y"][m].astype(float)
                    pr = dirs[m]
                    slot[s][0] += int((y == pr).sum())
                    slot[s][1] += int((y == -pr).sum())
                    slot[s][2] += int((y == 0).sum())
        print(f"param p={p} disp={dv} selesai", flush=True)

    names = sorted({k[0] for k in store})
    best: dict[str, tuple] = {}
    for name in names:
        for p, dv in grid:
            tr = store[(name, p, dv)]["train"]
            ntr = sum(tr)
            if ntr < MIN_TRAIN:
                continue
            wr = tr[0] / ntr
            if name not in best or wr > best[name][0]:
                best[name] = (wr, p, dv)

    print(f"\n{'aturan':20} {'p':>2} {'disp':>4} | {'TRAIN':>7} {'n':>7} | {'HOLD':>7} {'n':>7} {'lb95':>6} | "
          f"{'LOCK':>7} {'n':>7} {'lb95':>6} {'d(pt)':>6}")
    rows = []
    for name in names:
        if name not in best:
            continue
        _, p, dv = best[name]
        tr, ho, lb = (store[(name, p, dv)][s] for s in ("train", "holdout", "lockbox"))
        ntr, nho, nlb = sum(tr), sum(ho), sum(lb)
        if nho == 0 or nlb == 0:
            continue
        rows.append((name, p, dv, tr[0] / ntr * 100, ntr, ho[0] / nho * 100, nho,
                     H.wilson_low(ho[0], nho), lb[0] / nlb * 100, nlb,
                     H.wilson_low(lb[0], nlb), (lb[0] - lb[1]) / nlb * 100))
    rows.sort(key=lambda r: -r[5])
    for r in rows:
        print(f"{r[0]:20} {r[1]:2d} {r[2]:4.1f} | {r[3]:6.2f}% {r[4]:7d} | {r[5]:6.2f}% {r[6]:7d} {r[7]:6.2f} | "
              f"{r[8]:6.2f}% {r[9]:7d} {r[10]:6.2f} {r[11]:+6.2f}")
    if rows:
        ho = np.array([r[5] for r in rows])
        lb = np.array([r[8] for r in rows])
        print(f"\n{len(rows)} aturan kanonik: HOLD median={np.median(ho):.2f}% max={ho.max():.2f}% | "
              f"LOCK median={np.median(lb):.2f}% max={lb.max():.2f}%")
        print(f"lb95 holdout>50: {sum(1 for r in rows if r[7] > 50)}/{len(rows)}; "
              f"lb95 lockbox>50: {sum(1 for r in rows if r[10] > 50)}/{len(rows)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        t = np.arange(0, 60 * 500, 60.0)
        rng = np.random.default_rng(21)
        px = np.cumsum(rng.normal(0, 0.5, len(t))) + 100
        T = dict(t=t.astype(np.int64), o=px.copy(), h=px + 1, l=px - 1, c=px.copy(),
                 o5=px.copy(), h5=px + 1, l5=px - 1, c5=px.copy(), n5=np.full(len(t), 5),
                 n=np.full(len(t), 10), entry=np.roll(px, -1), closeb=np.roll(px, -1),
                 y=np.zeros(len(t), np.int8), ok=np.ones(len(t), bool))
        R = smc_canon(T, 1, 1.5)
        assert all(len(v) == len(t) for v in R.values()), "panjang aturan tidak sama"
        for v in R.values():
            assert set(np.unique(v)) <= {-1.0, 0.0, 1.0}, "arah harus -1/0/+1"
        ph, pl = _fractal(T["h"], T["l"], 1)
        sh_i, sh_p = _confirmed(ph, T["h"], 1)
        for i in range(5, 120):
            if sh_i[i] >= 0:
                assert sh_i[i] + 1 <= i, "lookahead: swing dipakai sebelum terkonfirmasi"
        ith_i, ith_p = _tier(np.isin(np.arange(len(t)), sh_i[sh_i >= 0]), T["h"], 1, True)
        assert np.isnan(ith_p[0]), "tier pertama harus NaN"
        print(f"selftest OK: {len(R)} aturan kanonik, tier & swing tanpa lookahead")
    else:
        main()
