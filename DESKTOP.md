# Desktop WPF

Executable distribusi: `desktop\dist\OpenTerminal.exe`.

## Perilaku

- Native WPF. Tidak memakai WebView atau browser UI.
- Menggunakan API lokal `127.0.0.1:4000`.
- Jika API belum aktif, desktop memulai `server\dist\index.js` lewat `node.exe` tanpa jendela `cmd.exe`.
- Market Explorer memakai satu scrollbar native di tepi kanan panel.
- Kolom `Price` dan status koneksi rata kanan serta lebarnya terkunci.
- Broker hanya DEMO, data-only.
- Chart menampilkan satu support hijau dan satu resistance merah terdekat dari pivot tertutup.
- Support dan resistance dihitung ulang dari candle broker pada setiap refresh satu detik; hanya pivot tertutup yang dipakai.
- Chart B.O.S/CHoCH hanya memberi satu panah pada candle break yang sudah tertutup. Candle aktif, bounce support, dan rejection resistance tidak membuat panah.
- Pivot B.O.S/CHoCH memakai tiga candle: satu candle kiri, pivot, dan satu candle kanan tertutup, agar lebih sensitif tanpa repaint.
- Panah struktur digambar pada candle setelah break terkonfirmasi; panah adalah penanda analisis, bukan jaminan entry atau hasil.
- Audit historis memisahkan marker: panah hanya untuk `BOS`; `CHoCH` tetap dianalisis tetapi tidak diberi panah karena hasil uji candle berikutnya lebih lemah.
- Refresh UI dilakukan setiap satu detik dari tick broker DEMO; candle M1 diperbarui dari tick yang diterima.
- Saat pindah market, chart lama disembunyikan. Latar hitam dan `LOADING ...` tetap tampil sampai dua candle valid selesai dirender; respons market lama tidak boleh mengganti chart baru.
- Kontrak ini sama untuk perubahan dari Codex, ChatGPT Desktop Work, maupun editor lain; rujuk `AGENTS.md` dan `STATUS.md` sebelum mengubah aplikasi.

## Build

```powershell
# PowerShell
 dotnet build desktop\OpenTerminal.Desktop.csproj -c Release --no-restore
Copy-Item desktop\bin\Release\net8.0-windows\OpenTerminal.* desktop\dist\ -Force
```

Jalankan `desktop\dist\OpenTerminal.exe` atau `Jalankan-App.cmd`.
