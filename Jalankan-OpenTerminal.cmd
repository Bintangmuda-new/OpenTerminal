@echo off
cd /d "%~dp0"
set "API_HOST=127.0.0.1"
set "API_PORT=4000"
call npm run start -w server
