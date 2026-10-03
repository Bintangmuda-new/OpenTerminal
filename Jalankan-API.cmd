@echo off
cd /d "%~dp0"
set "API_HOST=127.0.0.1"
set "API_PORT=4000"
set "POCKET_PYTHON=%~dp0server\.venv\Scripts\python.exe"
set "POCKET_BROWSER_PYTHON=C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\python.exe"
set "WEB_ORIGIN=http://localhost:3000"
call npm run start -w server >> api.log 2>&1
