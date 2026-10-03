# Audit strategi — hasil backtest

Tanggal: 26 September 2026
Data: `data/otc_research.sqlite3` — 357.649 candle M1, 142 simbol, 13–20 September 2026 (05:00 13 Sep – 23:32 20 Sep UTC)
Metode: split train (60% awal) / holdout (40% akhir). Peringkat dipilih dari train,
angka yang dilaporkan selalu dari holdout.

## Kesimpulan

**Tidak ditemukan strategi dengan edge yang bertahan di data baru.**

13 aturan diuji terhadap 122 simbol, ~101.000 trade holdout, dipecah per simbol,
per jam, per arah, dan per panjang streak. Tidak satu pun melewati 50% pada batas
bawah interval kepercayaan 95%.

Baseline feed: **50,04%** candle naik dari 100.938 sampel — netral sempurna.

## Bukti utama: uji gradien

Mean-reversion yang nyata akan menguat seiring streak memanjang. Yang terjadi
justru sebaliknya:

| streak | holdout% | n | batas bawah 95% |
|--------|----------|---|-----------------|
| 3 | 50,03% | 24.365 | 49,4% |
| 4 | 50,15% | 12.029 | 49,3% |
| 5 | 49,92% | 5.918 | 48,6% |
| 6 | 49,93% | 2.928 | 48,1% |
| 7 | 48,65% | 1.449 | 46,1% |
| 8 | 46,94% | 735 | 43,4% |

Datar lalu menurun. Itu kebalikan dari pola edge.

## Jebakan yang terhindar

1. **`ema cross`** — train 51,79% (terbaik), holdout 48,68%. Selisih 3 poin
   berlawanan arah. Tanpa split data, ini akan dilaporkan sebagai pemenang.

2. **`fade 5-streak`** — 53,02% pada sampel 5 simbol (149 trade), turun ke
   **49,92%** pada 122 simbol (5.918 trade). Sampel kecil menipu.

3. **`control: always CALL` @ UAHUSD_otc** — 54,79%, stabil. Bukan strategi:
   simbol itu sedang tren naik sepanjang periode data. Akan berbalik rugi begitu
   trennya habis.

Baris kontrol `always CALL` (50,11% global) mengalahkan 9 dari 12 strategi,
termasuk aturan yang dipakai panel SIGNAL (49,31%).

## Dua sistem sinyal diadu (backtest4.py)

Aplikasi sempat punya dua sinyal sekaligus. Diukur pada 122 simbol:

| sistem | holdout% | n | batas bawah 95% |
|--------|----------|---|-----------------|
| NEXT ENTRY score≥90 | 50,70% | 2.071 | 48,5% |
| NEXT ENTRY score≥80 | 49,96% | 2.364 | 47,9% |
| NEXT ENTRY score≥70 | 49,88% | 2.574 | 48,0% |
| CALL/PUT (EMA+RSI) | 49,81% | 17.897 | 49,1% |
| KEDUANYA SEPAKAT | 49,80% | 1.520 | 47,3% |

Tiga temuan:

1. Skor konfluensi 16 faktor (EMA, VWAP, MACD, Bollinger, pola candle, retest)
   **setara** dengan EMA+RSI sederhana. Kerumitan tidak menambah apa pun.
2. **Kolaborasi tidak menolong.** Kesepakatan dua sinyal justru 49,80% — di
   bawah keduanya. Tanda keduanya mengukur hal yang sama (momentum terkini).
3. Ambang lebih ketat (70→80→90) tidak menyeleksi, hanya mengecilkan sampel.

**Tindakan:** jalur NEXT ENTRY dihapus dari `MainWindow.xaml.cs` beserta
`ConfluenceScore`, `CompositeScore`, `DetectPattern`, `DrawStructureArrow`,
`CandlePattern`, `Macd`, `RsiSeries`, `CumulativeVwap` — total 101 baris.
Overlay struktur (BOS, CHoCH, SH/SL, level S/R) dipertahankan sebagai
informasi, tanpa klaim arah.

## Rebuild aturan sinyal (backtest5.py)

Jurnal live 115 menit di `#AAPL_otc` membongkar tiga cacat aturan EMA9/21:

- **45 sinyal / 115 candle** — 31 di antaranya berjarak 1 menit. Bukan seleksi,
  tapi berdagang tiap candle.
- **38 CALL vs 7 PUT** padahal candle entry naik 23 turun 21. Aturan nyangkut
  di satu sisi karena `close > EMA9` dengan EMA yang lambat mengikuti.
- Winrate live 43,2% dari 44 entry (CI 95%: 29–58%, belum beda signifikan dari 50%).

Kandidat pengganti diuji pada 122 simbol, dengan cooldown 5 candle:

| aturan | holdout% | batas bawah 95% | n | fire% | CALL/PUT |
|--------|----------|-----------------|---|-------|----------|
| **z>2 revert** | **51,16%** | **49,7%** | 4.783 | 14,6% | 97% |
| z3 + RSI agree | 51,38% | 48,2% | 975 | 3,0% | 99% |
| z>3 revert | 50,26% | 48,6% | 3.305 | 10,2% | 98% |
| live rule (EMA+RSI) | 49,47% | 48,2% | 5.638 | 17,1% | 95% |
| z>4 revert | 48,21% | 46,1% | 2.091 | 6,4% | 99% |
| exhaustion combo | 46,70% | 41,9% | 409 | 1,3% | 84% |

**Dipilih: `z>2 revert`** — CALL bila harga lebih dari 2× pergerakan rata-rata
di bawah SMA20, PUT bila di atasnya. Simetris secara konstruksi.

Konsisten saat diperluas (15 simbol 51,65% → 122 simbol 51,16%), tidak runtuh
seperti `fade 5-streak`. Tapi batas bawah 49,7% **masih di bawah 50** — belum
terbukti mengalahkan peluang acak.

Alasan penerapan bukan winrate, melainkan kualitas instrumen: sinyal 2,7× lebih
jarang dan seimbang arahnya, sehingga hasilnya bisa dinilai jujur alih-alih
tenggelam dalam 45 sinyal per 2 jam.

### Target 80%

Tidak tercapai pada feed ini. Bukti: 13+10 aturan diuji, tertinggi 51,38%,
baseline 50,04% dari 100.938 candle, uji gradien streak menurun. Menyetel
parameter sampai 80% muncul berarti memasang pola pada derau — kesalahan yang
membuat `ema cross` terlihat 51,79% lalu jatuh ke 48,68%.

## Upgrade 26 Sep 2026 — mikrostruktur tick & filter volatilitas

### Jurnal live terkini sebagai backtest

39 entry (#AAPL_otc 22, BTCUSD 17): 16 WIN, 22 LOSS, **41,0%**.
CI 95%: 26–58% — belum berbeda dari 50%. Arah sudah seimbang (20 CALL / 19 PUT),
cacat bias arah aturan EMA lama tidak terulang.

### backtest7_tick.py — 42 juta tick, wilayah yang belum pernah diuji

Semua uji sebelumnya memakai agregat M1. Ini memakai tick mentah pada titik
keputusan sebenarnya: **T-8 detik sebelum candle tutup**, hanya tick yang sudah
terlihat saat itu (tanpa lookahead). 10 fitur mikrostruktur: posisi dalam range
tick, dorongan 8 detik terakhir, skew up/down-tick, akselerasi paruh kedua,
pembalikan arah di tengah, kepadatan tick, dan tiga fitur candle sebelumnya.

| gerbang keyakinan | winrate | batas bawah 95% | n | kontrol |
|---|---|---|---|---|
| tanpa gerbang | 49,94% | 49,4% | 31.750 | 49,67% |
| \|p-0,5\| ≥ 0,02 | 50,21% | 49,6% | 21.968 | 49,49% |
| \|p-0,5\| ≥ 0,05 | 49,95% | 49,0% | 10.470 | 49,35% |
| \|p-0,5\| ≥ 0,08 | 50,20% | 48,7% | 4.056 | 48,78% |

**Tidak ada edge arah pada resolusi tick sekalipun.** Bobot model tertinggi
(`accel` −0,020) praktis nol.

### Uji chi-square: arah candle benar-benar independen

340.360 transisi arah, seluruh simbol:

- setelah TURUN → naik **50,04%**
- setelah NAIK → naik **49,85%**
- **chi² = 1,12** (kritis 3,84 pada p=0,05)

Hipotesis independen tidak bisa ditolak. Ini menutup pencarian prediksi arah:
generator broker tidak meninggalkan jejak pada urutan arah.

### Temuan yang berguna: volatilitas BISA diprediksi

Arah acak, tapi **ukuran** gerakan menggerombol. 150.734 candle, 40 simbol:

| kondisi | range besar berikutnya |
|---|---|
| setelah range KECIL | 47,17% |
| setelah range BESAR | **52,81%** |

Selisih 5,6 poin — jauh di luar derau, dan konsisten dengan volatility
clustering yang dikenal di pasar sungguhan.

### Penerapan: filter rezim volatilitas pada aturan z>2

Aturan z-score dipecah menurut rezim, dengan median dihitung **dari data train
saja** agar tidak bocor:

| segmen | rezim | winrate | batas bawah 95% | n |
|---|---|---|---|---|
| train | HI | 50,67% | 49,9% | 17.496 |
| train | LO | 49,90% | 49,0% | 11.995 |
| **holdout** | **HI** | **50,61%** | **49,7%** | 11.723 |
| holdout | LO | 49,34% | 48,2% | 7.866 |

**Bertahan di holdout** (50,67% → 50,61%, selisih 0,06 poin). Ini pertama
kalinya sebuah filter tidak runtuh seperti `ema cross` (−3,1 poin) atau
`fade 5-streak` (−3,1 poin).

**Diterapkan:** entry z>2 hanya diambil bila range candle berjalan di atas
median 60 candle terakhir. Rezim sepi → status `SKIP · VOL LO`.

Efek: sinyal berkurang ~40%, winrate holdout naik dari 50,10% ke 50,61%.
Batas bawah 49,7% **masih di bawah 50** — tetap ditandai UNPROVEN.

### Target 80% — penutupan

Setelah 23 aturan M1, model ML walk-forward, mikrostruktur tick 42 juta baris,
dan uji chi-square pada 340.360 transisi: **arah tidak dapat diprediksi pada
feed ini**. Angka tertinggi yang bertahan out-of-sample adalah 50,61%.

80% tidak dapat dicapai dengan memilih strategi. Yang bisa ditingkatkan adalah
kualitas seleksi — lebih sedikit entry, lebih terukur — bukan winrate.

### Cara mengulang

```bash
python server/backtest7_tick.py 6   # mikrostruktur tick, T-8s, walk-forward
python server/test_vol_filter.py    # jaga filter volatilitas
```

## Aturan yang dipakai panel SIGNAL sekarang

Reversi z-score: **CALL** bila `(close - SMA20) / rata-rata pergerakan < -2`,
**PUT** bila `> +2`. Cooldown 5 candle. → **51,16%** holdout (4.783 entry,
batas bawah 95% 49,7%).

Aturan lama `EMA9/21 + RSI 28–72` (49,47%) diganti 26 Sep 2026 setelah jurnal
live membongkar bias arah dan frekuensi berlebih.

Ditandai **UNPROVEN** di UI. Bukan rekomendasi, dan tidak boleh diperlakukan
sebagai terbukti — batas bawah kepercayaan masih di bawah 50%.

## Kenapa

Harga OTC Pocket Option dihasilkan algoritma broker, bukan pasar dengan
partisipan. Generator adalah bagian yang sepenuhnya dikendalikan broker — tidak
ada alasan meninggalkan pola yang bisa dieksploitasi di sana. Data ini konsisten
dengan arah candle yang independen dari riwayatnya.

## Cara mengulang

```bash
python server/backtest.py        # 13 aturan, ambang payout
python server/backtest2.py 5     # per simbol + per jam, winrate mentah
python server/backtest3.py       # uji gradien streak, semua simbol
python server/backtest4.py       # NEXT ENTRY vs CALL/PUT vs kolaborasi
python server/backtest5.py       # rebuild aturan: kandidat z-score, cooldown
```

Semua membuka database secara read-only.

## Belum diuji

- `raw_ticks` (resolusi sub-menit) — mikrostruktur bisa berbeda dari agregasi M1
- Rentang data hanya 8 hari; perilaku generator mungkin berubah antar periode
- Pengumpulan data berhenti 20 September 2026 — penulisnya hilang saat recovery
