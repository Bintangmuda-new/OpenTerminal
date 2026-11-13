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

## Pencarian lanjutan — batas atas dibuktikan (12 Okt 2026)

Tinjauan ulang seluruh ruang pencarian yang belum diuji, target eksplisit 70%.

### backtest8_hunt.py — 60+ kandidat aturan, peringkat di train, angka di holdout

Keluarga aturan yang diuji: z-score (ambang 1,5–6), RSI (14/20/30), Bollinger
re-entry, struktur BOS/CHoCH, streak fade, burst momentum, body/range, posisi
close dalam candle, gap, jam, dan kombinasi AND 2–3 faktor.

Hasil: satu-satunya aturan yang holdout-nya di atas 50 pada sampel layak adalah
`rsi20/80` — holdout **53,92%** (n=994, batas bawah 95% 50,82) melawan train
**49,82%**. Selisih 4 poin berlawanan arah: tanda tidak stabil, bukan edge.

### backtest9_walkforward.py — fold per hari (out-of-sample tiap hari)

Split per hari membongkar angka 53,92% sebagai artefak periode:

| | rsi20/80 |
|---|---|
| gabungan day-fold | **50,75%** |
| hari di atas 50 | 4/8 |
| konsentrasi | tidak terpusat di satu simbol |

Turun dari 53,92% ke 50,75% saat hari dijadikan fold. Persis pola `ema cross`
dan `fade 5-streak` pada audit sebelumnya.

### backtest10_sniper.py — tier "semua gerbang harus setuju"

Prediksi yang bisa dipalsukan: bila selektivitas membeli akurasi, hit rate harus
naik monoton seiring jumlah gerbang yang wajib sepakat.

| gerbang sepakat | holdout% | n | batas bawah 95% |
|---|---|---|---|
| 0 (arah apa pun) | 49,95% | 344.903 | 49,79 |
| 1 (z>2,5) | 50,02% | 151.761 | 49,77 |
| 2 (+RSI70) | 49,85% | 35.258 | 49,33 |
| 3 (+volHI) | 49,87% | 18.771 | 49,16 |
| 4 (+bearTurn) | 50,27% | 2.206 | 48,19 |
| 5 (+bodyBig) | 52,58% | 213 | 45,89 |
| 6 (+m5dn) | 50,00% | 122 | 41,26 |
| 7 (+levelNear) | — | 4 | — |

**Tidak monoton.** Lonjakan 52,58% hanya pada n=213 dengan batas bawah 45,89 —
persis derau sampel kecil yang sudah diperingatkan di `fade 5-streak`.

Mekanisme lain yang juga nol: magnet angka bulat (level_dist<0,25 → 50,78%;
<0,05 → 46,74%), jam terpilih (jam 23Z 51,58% dari 6.103, semua jam 49,98%), dan
seleksi simbol teratas di train (top 10% → holdout 50,09%; top 20% → 50,55%).

### backtest11_leadlag.py — feed bersama, driver bersama?

Bila semua simbol dihasilkan loop broker yang sama, gerakan satu simbol mungkin
mendahului yang lain. Diuji pada 14.762 pasangan berurutan; top pair di train
hanya 51,6% dan jatuh ke 48–51% di holdout. **Nol pasangan** mencapai train ≥55%.

### backtest12_loose_ml.py — regresi logistik 4 faktor, walk-forward per hari

Skor konfluensi 16 faktor terbukti setara EMA+RSI (audit sebelumnya), tapi itu
bukan klaim yang sama dengan "model kecil tak punya edge". Regresi logistik 4
faktor (z, RSI, posisi close, return/ATR) di-fit pada hari lain dan diuji pada
hari berjalan:

| margin keyakinan | walk-forward% | n | hari>50 |
|---|---|---|---|
| 0,00 | 50,25% | 97.172 | 7/8 |

Bobot akhir mendekati nol (terbesar −0,0076). Model tidak belajar apa pun.

### backtest13_ceiling.py — uji penentu: bisakah 70% dicapai sama sekali?

Ini menjawab pertanyaan yang menentukan, bukan rata-rata melainkan **maksimum**.

1. **Ceiling.** Sapuan padat ambang z (1,5–6) dan RSI (20–40) pada semua simbol,
   27 sel dengan n≥100. Sel terbaik yang ada di mana pun dalam data:
   **50,26%** (z>1,75, n=102.452, batas bawah 49,95). Sel di atas 70%: **0**.
2. **Null test.** Hasil tiap simbol diacak (menghancurkan seluruh pola, menjaga
   jumlah), sapuan identik x20: maksimum 50,26% — **sama persis** dengan data
   nyata. Sel "terbaik" itu semata-mata laju dasar masking-nya, bukan informasi.
3. **Aritmetika sampel.** Untuk batas bawah Wilson ≥70: n=400 butuh 74,50%;
   n=5.000 butuh 71,28%. Laju sejati harus ~75% agar terbukti.

**Kesimpulan: 70% tidak dapat dicapai pada feed ini.** Bukan soal memilih strategi
atau menyetel parameter — batas atas empiris yang ada di seluruh dataset adalah
50,26%, dan angka itu identik dengan tebakan acak.

### backtest14_lookahead.py — permintaan "tetapkan 100%, segala cara"

Permintaan eksplisit: capai 100% dengan cara apa pun. Hanya ada satu cara:
baca candle entry (B) sebelum terjadi. Sinyal = `sign(close_B − open_B)`.

| | winrate | n | batas bawah 95% |
|---|---|---|---|
| LOOK-AHEAD (pakai candle B) | **100,0000%** | 348.612 | 99,9989 |
| real-time jujur (pakai candle A) | 49,8992% | 343.649 | 49,7320 |

100% tercapai secara aritmetika: 348.612 dari 348.612 trade. Di feed live candle B
belum disiarkan, jadi aturan yang sama tidak bisa menyala.

**Dipasang di app sebagai `LOOK-AHEAD 100% · DEMO`** (pilihan ke-13, kode `L`),
berlabel jelas "tidak bisa live". Jaring pengaman: `index + 1 >= chartCandles.Count`
→ kembalikan null (fail closed), jadi di feed live panel menampilkan `NO SETUP`,
bukan crash dan bukan tebakan. Sinyal hanya muncul di playback historis.
Dijaga `server/test_lookahead.py`.

**Ini bukan edge dan bukan strategi.** Ini pembuktian bahwa angka 100% hanya bisa
dihasilkan dengan melihat masa depan — dan itu sebabnya semua keluarga aturan
jujur berhenti di 50,26%.

## Upgrade 25 Okt 2026 — keluarga TREND + ilmu dari repo SYAPOCKET

Permintaan: strategi TREND dengan winrate optimal, target 80-100%.

Diuji dengan protokol T-5 yang sama (`research_harness`), 4 skrip baru:

| skrip | keluarga uji | hasil terbaik (holdout hari 6) |
|---|---|---|
| `backtest29_trend.py` | 18 aturan tren x horizon 1/2/3/5 menit | 50,68% (h=5, *stack + \|z\|<1.5*, n=23.277) |
| `backtest30_trend_opt.py` | HTF M5/M15, sweep EQH/EQL, clearance 0,4 ATR, struktur HH/HL | 50,53% (*sweep+HTF*, n=3.705), lockbox 49,90% |
| `backtest31_app_trend.py` | aturan TREND app apa adanya (BOS + cross EMA + candle) | 49,58% (n=61.548) |
| `backtest32_trend_grid.py` | grid 697 kombinasi gerbang, seleksi TRAIN | **0 dari 697 mencapai 80%**; holdout terbaik 51,54% dari 200 kandidat terbaik TRAIN |
| `backtest34_app_timing.py` | kandidat tren, timing app, exit 1m & 5m | **51,73%** (train 50,63%, lockbox 50,57%) untuk *pullback resume* exit 5m |
| `backtest35_pullback.py` | pullback resume: sensitivitas z, per hari, null test | 51,15% holdout (z=1,0, n=5.873), lockbox 50,40%, null p95 51,00% → lolos tipis |
| `backtest36_trend_vs_pullback.py` | adu pada expiry 1m (setting app sekarang) | app 48,88% vs pullback z=1,0 50,20% (lockbox 49,41%) → tidak cukup |

Grid `backtest32`: peringkat diambil dari TRAIN (hari 0-5), vonis di HOLDOUT
(hari 6) dan LOCKBOX (hari 7). Kandidat dengan TRAIN tertinggi (52,72% /
53,20%, n 500-700) jatuh ke 48-49% di holdout — persis pola `ema cross` yang
sudah terdokumentasi. Kontrol `always CALL` 49,38% mengalahkan hampir semua
aturan tren.

`backtest13_ceiling.py` diulang: null test 20x identik dengan data nyata
(50,26% vs 50,26%) — sel terbaik di seluruh ruang parameter adalah artefak
seleksi. Batas bawah Wilson 95% ≥ 70% butuh true rate ~84% pada n=50 atau
~71% pada n=5.000. Feed ini tidak punya itu di keluarga mana pun yang diuji.

### Satu-satunya kandidat yang tidak runtuh: pullback resume (exit 5 menit)

Aturan: EMA 9/21/50 searah, harga tertutup menjauh > 1 unit pergerakan rata-rata
(z ≈ 1) melawan tren, lalu entry searah tren. Hasil diukur ke close menit i+5.

| segmen | winrate | n |
|---|---|---|
| train hari 0-5 | 50,63% | 8.941 |
| holdout hari 6 | **51,73%** | 6.047 |
| lockbox hari 7 | 50,57% | 10.028 |

Ambang z stabil (z=0,75→1,25: holdout 51,2-51,5%), bukan puncak runcing seperti
`fade 5-streak`. Null test per simbol 20x di holdout: nyata 51,15% vs p95 null
51,00% — lolos **tipis**. Pada expiry 1 menit (setting app sekarang) keunggulan
ini hilang: holdout 50,20%, lockbox 49,41%. Jadi ini kandidat durasi 5 menit,
bukan pengganti aturan 1 menit yang ada. Masih 4,5 poin di bawah break-even
payout 80% (55,6%) dan baru diuji pada 8 hari data.

**Ilmu baru dari repo `shaddvl-web/SYAPOCKET`** (ditelusuri 25 Okt 2026):
confluence 9 faktor 0-100 dengan gate 70% (SMC BOS/CHoCH 20%, HTF M5/M15 15%,
S/R clearance 0,4 ATR 15%, price action 15%, momentum 10%, liquidity sweep 10%,
indikator 5%, volatilitas 5%, expiration fit 5%). Yang belum pernah diuji di
proyek ini (HTF alignment, sweep EQH/EQL, clearance ATR, HH/HL) sudah diport
ke `backtest30_trend_opt.py` — hasilnya 49-50,5%. Sisa faktornya (MACD, pinbar,
marubozu, expiration fit) sudah tercakup di uji konfluensi 16 faktor lama yang
juga netral. Repo itu tidak membawa bukti holdout apa pun: `tests/` hanya unit
test, provider datanya Binance/Deriv/synthetic — bukan feed OTC Pocket Option,
dan gate 70% adalah ambang desain, bukan hasil pengukuran.

**Kesimpulan tidak berubah:** tidak ada strategi TREND dengan winrate 80% di
data ini. Angka >80% hanya bisa dipasang lewat seleksi sampel atau look-ahead;
`Look-Ahead 100% · DEMO` sudah ada di UI sebagai buktinya dan gagal-tertutup
di feed live.

### backtest15_github.py — strategi publik GitHub (18 Okt 2026)

Dicari via `gh search repos` ("binary options strategy", "pocket option bot",
"otc binary options", "iq option strategy", "quotex strategy"). Mayoritas repo
hanya bot sinyal Telegram/landing page berklaim "95% accuracy" tanpa kode atau
tanpa uji out-of-sample. Lima aturan yang punya logika eksplisit di-port apa
adanya (MFI dibuang: feed tanpa volume), sinyal dari candle A tertutup, dinilai
candle B, fold per hari. Self-test memastikan tidak ada lookahead.

| aturan (sumber) | winrate | batas bawah 95% | n | hari>50 |
|---|---|---|---|---|
| EMA9/20 + RSI band (`ADMusab12/OTC-Signal-Engine`) | 49,78% | 49,56 | 209.757 | 2/8 |
| RSI6 + CCI fade (`walicard56/backtest-iq-option`) | 50,40% | 49,68 | 18.707 | 7/8 |
| liquidity sweep (`richmelody15/catalyst-signals`) | 50,36% | 49,87 | 39.415 | 6/8 |
| Bollinger touch + RSI30/70 | 50,07% | 49,30 | 16.380 | 5/8 |
| Stochastic 14/3 cross OB/OS | 49,94% | 49,48 | 46.135 | 5/8 |
| kontrol: always CALL | 49,95% | 49,79 | 344.903 | 3/8 |

**Tidak satu pun melewati 50 pada batas bawah 95%**, apalagi break-even payout
80% (55,6%). Tertinggi 50,40% — di bawah `z>2 + vol HI` (50,61%) yang sudah
dipakai. Tidak ada yang layak menggantikan strategi aktif; app tidak diubah.

## Aturan yang dipakai panel SIGNAL sekarang

Reversi z-score: **CALL** bila `(close - SMA20) / rata-rata pergerakan < -2`,
**PUT** bila `> +2`. Cooldown 5 candle. → **51,16%** holdout (4.783 entry,
batas bawah 95% 49,7%).

Aturan lama `EMA9/21 + RSI 28–72` (49,47%) diganti 26 Sep 2026 setelah jurnal
live membongkar bias arah dan frekuensi berlebih.

**Ditambahkan 12 Okt 2026: `RSI EXTREME 20/80 (fade)`** di INDIKATOR > STRATEGI
(pilihan ke-12, kode `X`). CALL bila RSI<20, PUT bila RSI>80. Ini satu-satunya
aturan dari 60+ kandidat `backtest8`/`backtest9` yang holdout-nya bertahan di
atas 50 pada n layak, dan lolos 4/8 fold hari — angka gabungannya 50,75%.
**Di bawah target 70%; ditandai UNPROVEN.** Diterapkan bukan karena terbukti,
tetapi karena seleksinya paling jujur diuji.

Keduanya ditandai **UNPROVEN** di UI. Bukan rekomendasi, dan tidak boleh
diperlakukan sebagai terbukti — batas bawah kepercayaan masih di bawah 50%.

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
python server/backtest8_hunt.py      # 60+ kandidat, train 60% / holdout 40%
python server/backtest9_walkforward.py  # fold per hari + konsentrasi simbol
python server/backtest10_sniper.py   # tier 1..7 gerbang, magnet, jam, simbol
python server/backtest11_leadlag.py  # lead-lag antar simbol (14k pasangan)
python server/backtest12_loose_ml.py # regresi logistik 4 faktor walk-forward
python server/backtest13_ceiling.py  # batas atas + null test (bukti penentu)
python server/backtest14_lookahead.py # 100% via baca candle entry (demo, bukan edge)
python server/backtest15_github.py   # 5 strategi publik GitHub, day-fold + self-test lookahead
python server/test_rsi_extreme.py    # jaga aturan RSI 20/80
python server/test_lookahead.py      # jaga aturan look-ahead demo (fail closed)
python server/backtest29_trend.py    # 18 aturan TREND x horizon 1/2/3/5 menit (T-5)
python server/backtest30_trend_opt.py # HTF M5/M15, sweep, clearance ATR, HH/HL (T-5)
python server/backtest31_app_trend.py # aturan TREND app (decide di close candle)
python server/backtest32_trend_grid.py # grid 697 kombinasi gerbang, seleksi TRAIN
python server/backtest33_null_trend.py # null test kandidat terbaik h=5
python server/backtest34_app_timing.py # kandidat TREND dengan timing app (exit 1m & 5m)
python server/backtest35_pullback.py  # pullback resume: sensitivitas z, per hari, null test
python server/backtest36_trend_vs_pullback.py # adu h=1: aturan app vs pullback
```

Semua membuka database secara read-only.

## Belum diuji

- `raw_ticks` (resolusi sub-menit) — mikrostruktur bisa berbeda dari agregasi M1
- Rentang data hanya 8 hari; perilaku generator mungkin berubah antar periode
- Pengumpulan data berhenti 20 September 2026 — penulisnya hilang saat recovery

## Yang sudah ditutup (12 Okt 2026)

- ~~`raw_ticks` mikrostruktur~~ → `backtest7_tick.py`: 42 juta tick, T-8 detik, 50%
- ~~Regresi logistik~~ → `backtest12_loose_ml.py`: 4 faktor walk-forward, 50,25%
- ~~Lead-lag antar simbol~~ → `backtest11_leadlag.py`: 14.762 pasangan, nol ≥55%
- ~~Seleksi simbol/jam~~ → `backtest10_sniper.py`: top 10% train → holdout 50,09%
- ~~Ambang lebih ketat~~ → `backtest13_ceiling.py`: 27 sel, 0 sel ≥70%, null test identik

Yang tersisa hanya data lebih panjang. Selama generator broker tidak berubah
perilaku, hasilnya akan tetap 50%.

---

# Audit SMC/ICT (13 Nov 2026)

Data: cache riset `E:\otc_cache\min` — 142 simbol, **346.737 candle M1**,
13–20 September 2026. Keputusan T-5s, entry = open candle B, hasil = close
candle B (identik `research_harness`).

Protokol: **train hari 0–5 / holdout hari 6 / lockbox hari 7**. Semua pemilihan
dilakukan di train; lockbox dibuka sekali untuk vonis.

## Temuan 1 — CHoCH murni tidak punya edge

18 aturan SMC/ICT kanonik × 4 parameter (p, disp) = 716 kombinasi, dievaluasi
pada **1.013.216 sinyal**:

| aturan | n holdout | WR holdout | lb95 |
|--------|-----------|------------|------|
| K1 CHoCH kanonik | 158.720 | 51,20% | 50,96 |
| K15 seq_htf_choch | 84.425 | 51,46% | 51,12 |
| K17 seq_full | 2.369 | 54,16% | 52,15 |
| K12 HTF H1 | 213.662 | 49,60% | 49,39 |

Yang tampak "menang" di holdout **gagal di train** (K17: train 49,72%). Itu
tanda overfit, bukan edge.

## Temuan 2 — KOREKSI: uji null saya yang pertama salah

Bagian ini menggantikan klaim "drift" yang saya tulis sebelumnya. Klaim itu keliru
dan penting untuk dicatat kenapa.

Uji null pertama mengacak **label hasil (`y`)** di dalam subset sinyal yang sudah
terpilih. Mengacak `y` di dalam satu subset **tidak mengubah rata-ratanya** — jadi
uji itu secara matematis tidak mungkin menolak apa pun, dan "p95 = winrate asli"
adalah artefak, bukan bukti drift.

Uji yang benar: acak **mask gate** di dalam grup simbol+hari, sehingga ukuran subset
terjaga tetapi anggotanya berubah. Hasilnya:

| kandidat | n | WR asli | null mean | null p95 | p-value |
|---|---|---|---|---|---|
| OTE saja | 14.602 | 55,02% | 51,47% | 52,13% | 0,000 |
| OTE+FVG | 1.222 | 60,23% | 51,63% | 53,88% | 0,000 |
| OTE+sweep | 1.822 | 58,40% | 51,48% | 53,39% | 0,000 |
| OTE+FVG+sweep | 374 | 60,43% | 51,55% | 55,77% | 0,000 |

Semua gate lolos. Membandingkan dengan "semua sinyal di grup yang sama"
(51,58%–51,81%) menunjukkan gap 3,5–8,6 poin yang nyata.

**Pelajaran metodologi:** uji null harus mengacak **mekanisme seleksi**, bukan
hasil di dalam seleksi. Versi pertama terlihat meyakinkan dan menghasilkan
kesimpulan yang sepenuhnya salah.

## Temuan 3 — OTE adalah edge nyata (satu-satunya)

Uji 4 sel pada sinyal yang sama (p=2, tanpa lookahead):

| sel | n holdout | WR holdout | lb95 |
|-----|-----------|------------|------|
| OTE saja (tanpa CHoCH) | 51.231 | 50,16% | 49,73 |
| **OTE + CHoCH** | **9.549** | **55,45%** | **54,45** |
| CHoCH saja | 154.149 | 51,42% | 51,17 |
| tanpa keduanya | 238.532 | 49,05% | 48,85 |

Kesimpulan: **trigger CHoCH hanya bernilai bila entry berada di zona diskon
(retracement 62–79% = OTE).** Sendiri-sendiri keduanya ~50%.

Bukti tambahan:
- Permutasi label OTE dalam simbol×hari: p-value **0,0000** (1000 iterasi)
- Positif di **7/7 hari** (rata-rata +2,79pt, t=4,42)
- **98 dari 123 simbol** (80%) winrate > 50%
- **90 dari 123 simbol** (73%) melewati impas payout 92%

Catatan penting: edge ini hanya muncul di **CALL**, tidak di PUT
(PUT+OTE = 44,72%). Penyebabnya belum dipastikan — kandidat: bias naik
jangka pendek pada jendela data. **Perlakukan sebagai hipotesis yang perlu
konfirmasi di periode lain.**

## Temuan 4 — dua bug di aplikasi

### Bug A: CHoCH asimetris (`AnalyzeSmcFull`)

`lowerHigh` diisi `lastSth` (swing high **sebelumnya**, lebih tinggi),
sedangkan `higherLow` diisi `pivotLow` (pivot terbaru). Akibatnya CHoCH
bullish harus menembus level yang lebih jauh daripada bearish.

Terbukti pada data: sinyal CALL hanya **31.529** vs PUT **51.076** di train
(rasio 0,62). Setelah disimetriskan: 46.018 vs 51.076 (rasio 0,90).
Diverifikasi ulang di harness C#: rasio CALL/PUT **0,56 → 1,01**.

### Bug B: `AggregateCandles` tidak sejajar jam

Loop lama `for (index = 0; index + factor <= Count; index += factor)`
mengelompokkan dari **elemen pertama array**, bukan batas waktu. Karena daftar
candle bergeser setiap bar baru, batas M15/H1 ikut bergeser → swing, CHoCH,
dan FVG berubah tanpa alasan pasar.

Diperbaiki menjadi bucket `floor(Time / (60*factor))`. Diverifikasi: 100 candle
M1 mulai 12:07:23 → 7 bucket, semuanya sejajar batas 900 detik.

## Temuan 5 — struktur GRADE (implementasi final)

Sinyal tidak lagi biner. Konfluensi OB/FVG diukur dan dipakai memberi grade,
sehingga trader tahu kekuatan tiap sinyal tanpa harus menebak.

| grade | komposisi | n | WR | min antar-periode | EV92 | /hari |
|---|---|---|---|---|---|---|
| **A** | OTE + FVG + OB | 177 | **64,41%** | 57,14% | +23,66% | 22 |
| **B** | OTE + FVG | 1.045 | **59,52%** | 56,27% | +14,28% | 131 |
| C | OTE saja | 13.380 | 54,54% | 53,50% | +4,72% | 1.673 |

"min antar-periode" = winrate terendah di antara train/holdout/lockbox. Grade A
tetap 57,14% bahkan di periode terburuknya.

**Yang dipakai sebagai sinyal: hanya A dan B.** Grade C (54,54%) tidak
dijadikan sinyal karena marginnya terlalu tipis untuk dipertaruhkan.

Komponen yang **tidak** menambah nilai dan karena itu tidak dipakai:
- BOS: menurunkan winrate
- HTF H1: 55,19% vs 55,02% — dalam batas noise
- sweep: dalam OTE+FVG hanya 60,43% vs 60,14% — tidak signifikan

## Temuan 6 — PUT tidak bisa diselamatkan

Enam konfigurasi diuji untuk sisi PUT, semuanya gagal:

| konfigurasi PUT | n | WR |
|---|---|---|
| baseline semua CHoCH | 253.194 | 48,28% |
| + OTE | 14.896 | 44,72% |
| + OTE + H1 turun | 7.750 | 44,44% |
| + H1 turun saja | 127.802 | 47,98% |
| + OTE + FVG + H1 | 718 | 41,78% |
| + OTE + OB + H1 | 1.409 | 45,14% |

Tidak ada satu pun yang mencapai impas 52,08%. Karena itu aplikasi **tidak
pernah** mengeluarkan sinyal PUT. Diverifikasi lewat harness: 54 kombinasi
masukan, sinyal SELL muncul 0 kali.

## Perubahan yang diterapkan

1. `AnalyzeSmcFull`: `lowerHigh = pivotHigh` (simetris dengan `higherLow`)
2. `AggregateCandles`: pengelompokan menurut batas waktu
3. **Sistem GRADE menggantikan sinyal biner.** Sinyal hanya keluar pada grade
   A (OTE+FVG+OB, 64,41%) atau B (OTE+FVG, 59,52%). Grade C (OTE saja) dan
   semua sinyal PUT tidak pernah menjadi sinyal. Field `Grade` dan `GradeNote`
   ditambahkan ke `SmcAnalysis`.
5. **Overlay chart dirapikan**: zona OTE kini digambar sebagai area entry
   (bukan hanya premium/discount samar), label OB/FVG dipindah ke kiri agar
   tidak bertumpuk dengan panel teks, dan panel kanan menampilkan
   BIAS/ZONA/CHoCH/OTE/GRADE/SINYAL dalam satu blok.
4. Perbaikan build yang sudah rusak sebelumnya: `UiSettings` kehilangan
   `OrderMinAmount`, `demoOrderMinAmount` tidak dideklarasikan.

Dampak filter pada sinyal aplikasi (p=1, 502k sinyal):

| arah | sebelum | sesudah OTE | lolos |
|------|---------|-------------|-------|
| CALL | 51,55% | **54,42%** (+2,87pt) | 7,3% |
| PUT  | 48,18% | 45,05% (−3,13pt) | 7,2% |

Build: **0 error, 0 warning.**

## Batasan yang harus diingat

- Data riset berhenti **20 Sep 2026**; jurnal live berjalan **4–12 Nov 2026**.
  Keduanya **tidak tumpang tindih** — edge OTE belum diuji di periode live.
- Jendela hanya 8 hari. Generator broker bisa berubah perilaku.
- Edge hanya di CALL; arah PUT belum punya dasar.
- Payout 92% butuh winrate > 52,08%. OTE+CHoCH 55,45% memberi EV +3,2%,
  tetapi dengan n=9.549 holdout marginnya tipis.

## Skrip

```
_smc_lab/smc_lab.py         # 18 aturan x 4 param, train/holdout
_smc_lab/matriks_sinyal.py  # bangun 1.013.216 sinyal + 9 gate
_smc_lab/grid_aturan.py     # 716 kombinasi gate, seleksi train
_smc_lab/validasi_final.py  # lockbox + per simbol + null test
_smc_lab/uji_drift.py       # pisahkan edge dari drift
_smc_lab/uji_ote2.py        # permutasi label OTE (p-value)
_smc_lab/uji_ote_murni.py   # uji 4 sel: OTE vs CHoCH vs keduanya
_smc_lab/uji_kelayakan.py   # sebaran simbol + EV per payout
_smc_lab/uji_asimetri.py    # kenapa OTE hanya CALL
_smc_lab/uji_konsekutif.py  # fresh vs chained
_smc_lab/verify/            # harness C# untuk 2 bug
```
