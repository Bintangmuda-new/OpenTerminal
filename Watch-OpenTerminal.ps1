$ErrorActionPreference = 'SilentlyContinue'
$root = 'E:\JANGAN DI HAPUS\OpenTerminal'
$log = Join-Path $root 'watchdog.log'
$mutex = [Threading.Mutex]::new($false, 'OpenTerminalWatchdog')
if (-not $mutex.WaitOne(0)) { exit 0 }

function Write-Health([string]$message) {
  Add-Content -LiteralPath $log -Value "$(Get-Date -Format s) $message"
}

function Start-Local([string]$launcher) {
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $launcher -WorkingDirectory $root -WindowStyle Hidden
  Write-Health "started=$launcher"
}

function Port-Listening([int]$port) {
  return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function App-Running {
  return $null -ne (Get-Process -Name 'OpenTerminal' -ErrorAction SilentlyContinue | Select-Object -First 1)
}

try {
  while ($true) {
    if (-not (Port-Listening 4000)) { Start-Local 'Jalankan-API.cmd' }
    if (-not (App-Running)) { Start-Local 'Jalankan-App.cmd' }
    Start-Sleep -Seconds 15
  }
} finally {
  $mutex.ReleaseMutex() | Out-Null
  $mutex.Dispose()
}
