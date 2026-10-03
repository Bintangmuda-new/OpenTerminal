# Cek-Runtime-Openterminal.py — diagnosa kenapa OpenTerminal.exe tidak kebuka
# Jalankan:
#   python Cek-Runtime-Openterminal.py
# Hasil ditulis ke file Cek-Runtime-Result.txt dan JSIGN di layar.

import os, subprocess, sys, glob, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "Cek-Runtime-Result.txt")

def s(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           creationflags=0x08000000)  # CREATE_NO_WINDOW
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"ERROR: {e}"

lines = []
def log(*a):
    t = " ".join(str(x) for x in a)
    lines.append(t)
    print(t)

def check_rt(name):
    for root in (os.environ.get("ProgramFiles", "C:\\Program Files"),
                 os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")):
        d = os.path.join(root, "dotnet", "shared", name)
        if os.path.isdir(d):
            v = [x for x in os.listdir(d) if x[0].isdigit()]
            return d, v
    return None, []

def main():
    log("=" * 50)
    log(f"Diagnosa OpenTerminal - {datetime.datetime.now():%Y-%m-%d %H:%M}")
    log("=" * 50)

    log("\n[1] Python:", sys.version.split()[0])

    log("\n[2] dotnet --list-runtimes:")
    log(s(["dotnet", "--list-runtimes"]))

    log("\n[3] WindowsDesktop.App (butuh 8.0.x):")
    d, v = check_rt("Microsoft.WindowsDesktop.App")
    log(f"  Path : {d or 'MISSING - RUNTIME DESKTOP TIDAK TERINSTALL'}")
    log(f"  Versi: {', '.join(v) or 'KOSONG'}")

    log("\n[4] Microsoft.NETCore.App:")
    d4, v4 = check_rt("Microsoft.NETCore.App")
    log(f"  Path : {d4 or 'MISSING'}")
    log(f"  Versi: {', '.join(v4) or 'KOSONG'}")

    log("\n[5] Proses OpenTerminal sedang jalan?")
    log(s(["tasklist", "/fi", "imagename eq OpenTerminal.exe"]))

    exe = os.path.join(BASE, "desktop", "dist", "OpenTerminal.exe")
    log("\n[6] EXE target:")
    if os.path.isfile(exe):
        log(f"  ADA, {os.path.getsize(exe):,} bytes, mtime {datetime.datetime.fromtimestamp(os.path.getmtime(exe)):%Y-%m-%d %H:%M}")
        log(f"  self-contained? cek runtimeconfig:")
        rc = exe.replace(".exe", ".runtimeconfig.json")
        if os.path.isfile(rc):
            log(s(["type", rc]))
    else:
        log(f"  MISSING: {exe}")

    log("\n[7] Crash log (15 baris terakhir):")
    cl = os.path.join(BASE, "desktop", "dist", "OpenTerminal-crash.log")
    if os.path.isfile(cl):
        for line in open(cl, encoding="utf-8", errors="replace").read().splitlines()[-15:]:
            log("  |", line)
    else:
        log("  Tidak ada crash log")

    log("\n[8] Ada runtime self-contained di folder dist?")
    selfc = [f for f in glob.glob(os.path.join(BASE, "desktop", "dist", "*")) if "runtimeconfig" in f or "coreclr" in f or "hostfxr" in f.lower()]
    log(f"  {selfc or 'TIDAK ADA (build butuh runtime terinstall)'}")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    log(f"\nSELESAI. Hasil juga di: {OUT}")

if __name__ == "__main__":
    main()
    try:
        import os
        os.system(f'notepad "{OUT}"')
    except Exception:
        pass