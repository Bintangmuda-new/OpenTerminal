$root = 'E:\JANGAN DI HAPUS\OpenTerminal'
$app = Join-Path $root 'desktop\dist\OpenTerminal.exe'

# Compatibility entry point: API monitoring now runs inside the desktop app.
if (Test-Path $app) {
  Start-Process -FilePath $app -ArgumentList '--tray' -WorkingDirectory $root -WindowStyle Minimized
}
else {
  Start-Process -FilePath 'dotnet' -ArgumentList 'run', '--project', 'desktop\OpenTerminal.Desktop.csproj' -WorkingDirectory $root
}
