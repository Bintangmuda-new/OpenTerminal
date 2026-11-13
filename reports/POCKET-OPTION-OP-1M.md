# Riset Pocket Option OP 1M

Tanggal audit: 2026-10-18. Semua sumber dibaca tanpa mengubah data mentah.

## Temuan data

- Cache tick: 142 arsip NPZ, 42,409,629 tick, 678,623,644 byte, 142 simbol; rentang 2026-09-13 05:24:33 UTC – 2026-09-20 23:32:01 UTC.
- Pemeriksaan tick: nilai invalid 0; waktu mundur 0; timestamp duplikat per simbol 0; arsip rusak 0.
- Cache candle hasil olahan: 357,649 bar M1; pasangan T-5/entry/close valid 351,067.
- CSV terpisah `ext_vitaly`: 47 file, 9,243 baris, 25 simbol, rentang 2024-02-02 16:02:00 UTC – 2024-02-06 20:43:00 UTC; bukan dataset 42 juta tick.
- CSV: 0 baris malformed, 388 baris OHLC tidak konsisten, timestamp duplikat 0, mundur 0.
- Database SQLite aplikasi berisi 42,409,629 baris `ticks` dan 42,343,834 `raw_ticks`; jurnal hasil live `signal_outcomes` kosong.
- Arsip tick memiliki waktu epoch dan harga; bid/ask serta bukti kecocokan harga penyelesaian dengan Pocket Option tidak tersedia. Hasil adalah proksi historis, bukan hasil broker terverifikasi.

## Protokol

- Keputusan pada candle A di T-5 detik: fitur hanya memakai candle yang sudah tutup dan tick A dengan offset sampai 55 detik.
- Entry: tick valid pertama pada candle B berikutnya; hasil: tick terakhir candle B dibandingkan entry.
- Naik = CALL menang, turun = PUT menang, sama = DRAW. Draw dihitung terpisah dan tidak menjadi win.
- Pemisahan waktu nominal: train hari 0–5, holdout hari 6, dan hari 7. Namun, hasil hari 7 sudah diperiksa selama eksperimen strategi terdahulu, lalu dipakai dalam pemilihan/peninjauan strategi. Karena itu hari 7 bukan lockbox independen; seluruh hasil di bawah bersifat deskriptif.
- Parameter `p=1, displacement=1.5` awalnya dicari di train. Strategi `K15_seq_htf_choch` kemudian dipilih setelah beberapa eksperimen SMC dan paparan hasil hari 7. Interval Wilson dan bootstrap blok 5-menit hanya menggambarkan variasi sampel, bukan validasi independen.

## Hasil pengujian retrospektif

| Periode | Sinyal | Win | Loss | Draw | Win rate | Wilson 95% | CALL menang | PUT menang |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| hari 0–5 | 41,613 | 21,184 | 19,842 | 587 | 50.91% | 50.43–51.39% | 10,599/20,936 | 10,585/20,677 |
| hari 6 | 27,462 | 14,085 | 13,121 | 256 | 51.29% | 50.70–51.88% | 7,191/14,219 | 6,894/13,243 |
| hari 7 (terpapar) | 51,699 | 26,599 | 24,572 | 528 | 51.45% | 51.02–51.88% | 12,842/24,998 | 13,757/26,701 |

Hari 7 mencakup 122 simbol dan 238 bucket lima-menit. Bootstrap blok 95% deskriptif: 50.97–51.92%. Ini bukan interval dari test independen.

## Pemeriksaan pembanding dan putusan

- SMC kanonik: hari 7 mencatat 51.45% (26,599/51,699; 24,572 loss; 528 draw). Jauh dari target 100%; karena hari 7 telah terekspos saat iterasi, angka ini bukan performa test independen. Sampel juga hanya satu hari dan simbol saling berkorelasi.
- RSI, z-score, price-action, momentum, logistic regression, kNN, dan 14 aturan SMC kanonik sudah diuji dalam runner `backtest16_reverse.py`, `backtest17_patterns.py`, `backtest20_knn_verify.py`, dan `backtest26_smc_canon.py`; hasil model/aturan lain berada dekat 50%.
- Kontrol yang membaca close candle B mencapai 100% karena memakai label masa depan. Itu kebocoran informasi, bukan strategi yang dapat dipakai sebelum entry (`backtest27_lookahead_demo.py`).
- **Target win rate 100% tidak terbukti.** Tidak ada test independen yang tersisa pada dataset ini. Strategi belum tervalidasi untuk forward testing; data hanya delapan hari, feed tidak dicocokkan dengan settlement broker, dan hasil model lintas strategi pernah ditinjau selama iterasi.
- CSV 2024 terlalu kecil untuk validasi model dan memiliki ketidaksesuaian header OHLC pada dua berkas OTC; baris mentah tidak diperbaiki atau dimasukkan ke pengujian tick.

## Reproduksi

```powershell
server\.venv\Scripts\python.exe server\op1m_research.py
server\.venv\Scripts\python.exe server\backtest26_smc_canon.py selftest
server\.venv\Scripts\python.exe -m unittest discover -s server -p 'test_*.py'
```
