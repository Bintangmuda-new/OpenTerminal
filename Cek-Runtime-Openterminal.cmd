@echo off
setlocal EnableDelayedExpansion
title Cek Runtime OpenTerminal
cd /d "%~dp0"

set "OUT=%~dp0Cek-Runtime-Result.txt"
echo ============================================ > "%OUT%"
echo Cek Runtime OpenTerminal - %date% %time% >> "%OUT%"
echo ============================================ >> "%OUT%"
echo. >> "%OUT%"

echo [1] dotnet --list-runtimes >> "%OUT%"
dotnet --list-runtimes >> "%OUT%" 2>&1
echo. >> "%OUT%"

echo [2] List shared runtimes (folder) >> "%OUT%"
for %%R in ("%ProgramFiles%\dotnet\shared\Microsoft.WindowsDesktop.App" "%ProgramFiles(x86)%\dotnet\shared\Microsoft.WindowsDesktop.App") do (
  if exist "%%~R" (
    echo Path: %%~R >> "%OUT%"
    dir /b "%%~R" >> "%OUT%" 2>&1
  ) else (
    echo MISSING: %%~R >> "%OUT%"
  )
)
for %%R in ("%ProgramFiles%\dotnet\shared\Microsoft.NETCore.App" "%ProgramFiles(x86)%\dotnet\shared\Microsoft.NETCore.App") do (
  if exist "%%~R" (
    echo Path: %%~R >> "%OUT%"
    dir /b "%%~R" >> "%OUT%" 2>&1
  ) else (
    echo MISSING: %%~R >> "%OUT%"
  )
)
echo. >> "%OUT%"

echo [3] Proses OpenTerminal sedang jalan? >> "%OUT%"
tasklist /fi "imagename eq OpenTerminal.exe" >> "%OUT%" 2>&1
echo. >> "%OUT%"

echo [4] Berat file EXE target >> "%OUT%"
if exist "desktop\dist\OpenTerminal.exe" (
  dir "desktop\dist\OpenTerminal.exe" >> "%OUT%" 2>&1
) else (
  echo MISSING: desktop\dist\OpenTerminal.exe >> "%OUT%"
)
echo. >> "%OUT%"

echo [5] Crash log (18 baris terakhir) >> "%OUT%"
if exist "desktop\dist\OpenTerminal-crash.log" (
  powershell -NoProfile -Command "Get-Content -Tail 18 'desktop\dist\OpenTerminal-crash.log'" >> "%OUT%" 2>&1
) else (
  echo Tidak ada crash log >> "%OUT%"
)

echo. >> "%OUT%"
echo SELESAI. Hasil tersimpan di: %OUT% >> "%OUT%"
start notepad "%OUT%"