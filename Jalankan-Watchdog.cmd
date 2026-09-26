@echo off
start "OpenTerminal Watchdog" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Watch-OpenTerminal.ps1"
