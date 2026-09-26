@echo off
cd /d "%~dp0"
if exist "desktop\dist\OpenTerminal.exe" start "" "desktop\dist\OpenTerminal.exe"
if not exist "desktop\dist\OpenTerminal.exe" dotnet run --project desktop\OpenTerminal.Desktop.csproj
