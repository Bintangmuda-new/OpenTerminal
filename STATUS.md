# Status Proyek

Diperbarui: 2026-10-05

## Siap Digunakan

- Desktop WPF native berjalan dari `desktop/dist/OpenTerminal.exe`.
- Shortcut desktop `OTerminal.lnk` mengarah ke build di `E:\JANGAN DI HAPUS\OpenTerminal\desktop\dist\OpenTerminal.exe`.
- Market Explorer: kolom simbol/nama, `Price`, dan status koneksi terkunci; `Price` serta status rata kanan.
- Market Explorer memakai satu scrollbar native pada tepi kanan panel. Tidak ada nested scrollbar atau horizontal scroll.
- Footer `Market Feed` dihapus.
- API lokal memakai `127.0.0.1:4000`; desktop menjalankan `node.exe` secara tersembunyi bila API belum aktif. Tidak lagi membuka launcher `cmd.exe`.
- Broker hanya mode DEMO dan data-only; tidak ada order, BUY/SELL, deposit, atau withdrawal.

## Chart Binary Option — Struktur Non-Repaint

- Pivot `SH`/`SL` valid setelah satu candle kanan tertutup (window tiga candle), agar B.O.S/CHoCH lebih sensitif tanpa repaint.
- `BOS` dan `CHoCH` hanya dibuat saat candle tertutup menembus swing terkonfirmasi dengan `close`, bukan wick atau candle aktif.
- Setelah break, level swing dikonsumsi agar tidak menghasilkan label BOS duplikat pada tick/candle berikutnya.
- Satu garis `SUPPORT` hijau dan satu garis `RESISTANCE` merah terdekat berasal dari swing terkonfirmasi timeframe penuh, auto-update setiap refresh, dan tetap tampil pada chart.
- Satu panah B.O.S/CHoCH muncul hanya pada candle break yang telah tertutup. Panah bounce support dan rejection resistance dinonaktifkan agar tidak ada double arrow.
- Panah struktur ditempatkan pada candle berikutnya setelah break terkonfirmasi oleh close; marker tidak berubah pada tick candle aktif.
- Audit historis candle berikutnya: `BOS` uji out-of-sample `50,73%` (`7.465` sampel); `CHoCH` `49,28%` (`7.119` sampel). Panah hanya menampilkan BOS; ini bukan jaminan hasil atau sinyal entry.
- Garis `BOS LEVEL`/`CHoCH LEVEL` ditampilkan sebagai referensi break/retest.
- Tren struktur aktif ditampilkan sebagai `UPTREND`, `DOWNTREND`, atau `RANGE`.
- Implementasi tidak memasang dependensi eksternal. Riset pembanding: `sadratech/Market-Structure-BOS-CHoCH` di GitHub; pola yang digunakan adalah pivot terkonfirmasi, break berdasarkan close, dan klasifikasi dari tren sebelumnya.

## Loading Chart Saat Pindah Market

- Saat simbol berubah, chart lama dibersihkan dan disembunyikan.
- Area chart tetap hitam dengan teks `LOADING ...` sampai sedikitnya dua candle valid tersedia.
- Chart baru tampil hanya setelah canvas selesai dirender; respons snapshot simbol lama ditolak agar chart tidak macet atau menampilkan data salah.

## Konsistensi Lintas Tool

- Kontrak proyek ini berlaku sama saat dikerjakan melalui Codex, ChatGPT Desktop Work, atau editor lain: WPF native, Pocket Option DEMO, dan data-only.
- Sumber kebenaran adalah file dalam repository ini, terutama `AGENTS.md`, `STATUS.md`, `DESKTOP.md`, dan `docs/architecture/`.
- Jangan mengubah kontrak broker, strategi, atau API tanpa memperbarui dokumentasi terkait dan menjalankan validasi yang tercantum di `AGENTS.md`.

## Database Riset

- `data/otc_research.sqlite3` menyimpan jurnal lokal `raw_ticks`, `ticks`, `candles_m1`, dan `markets`.
- SQLite memakai WAL. Checkpoint terakhir bersih; database tidak dihapus atau di-commit.
- Analisis database besar harus read-only dan bertahap agar tidak mengganggu feed live.
## Validasi Terakhir

```powershell
# Desktop
 dotnet build desktop\OpenTerminal.Desktop.csproj -c Release --no-restore
```

Hasil (2026-10-05): sukses, `0` warning, `0` error. API `DEMO` `status=live`.

### Insiden 2026-10-05: crash saat start

- Gejala: `OpenTerminal.exe` langsung keluar (Event 1023 `.NET Runtime`: *You must install or update .NET*), watchdog me-restart berulang.
- Root cause: `desktop\dist` tercampur build framework-dependent baru dan sisa publish self-contained lama (`hostfxr.dll`, `coreclr.dll`), sehingga host mencari runtime di `dist\`.
- Fix: `dist` diganti seluruhnya dengan output Release bersih; `dist` lama diarsip ke `_scratch\dist_broken_20261005`.
- Aturan: selalu kosongkan `desktop\dist` sebelum copy/publish (lihat `DESKTOP.md`). Cek cepat: `Cek-Runtime-Openterminal.cmd`.
- Pembersihan: 22 folder build eksperimen `desktop\dist-*`/`desktop\run-*` (±429 MB) dan `MainWindow.xaml(.cs).bak` diarsip ke `_scratch\old-builds-20261005`. Satu-satunya build aktif: `desktop\dist`.

## Struktur

```text
OpenTerminal/
├── desktop/                         WPF native Windows
│   ├── MainWindow.xaml              layout dan scrollbar Market Explorer
│   ├── MainWindow.xaml.cs           API launcher + candle M1 + B.O.S/CHoCH non-repaint
│   ├── OpenTerminal.Desktop.csproj  project .NET 8 WPF
│   └── dist/                        executable distribusi
├── server/                          Express API lokal
│   ├── src/                         TypeScript source
│   ├── dist/                        Node runtime build
│   ├── pocket_feed.py               satu-satunya WebSocket broker DEMO
│   └── pocket_client/               transport dan model broker
├── web/                             dashboard Next.js opsional
├── data/                            kredensial rahasia + jurnal SQLite lokal
│   └── otc_research.sqlite3         raw tick dan candle M1 (WAL)
├── docs/                            arsitektur dan koneksi
├── Jalankan-App.cmd                 launcher desktop
├── Jalankan-API.cmd                 launcher API manual
├── README.md                        dokumentasi umum
├── DESKTOP.md                       dokumentasi desktop
└── STATUS.md                        status ini
```

## Jalankan

- Desktop: `desktop\dist\OpenTerminal.exe` atau shortcut `OTerminal.lnk`.
- API manual: `Jalankan-API.cmd`.
- Web opsional: `Jalankan-Web.cmd`.

`data/` berisi secret lokal. Jangan dipindahkan, dibagikan, atau di-commit.
