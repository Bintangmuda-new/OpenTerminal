@echo off
cd /d "%~dp0"
set "ANTHROPIC_API_KEY="
set "API_HOST=127.0.0.1"
set "API_PORT=4000"
set "ANTHROPIC_BASE_URL=http://127.0.0.1:20128"
set "ANTHROPIC_MODEL=claude-BMF_COWORK"
call npm run start -w server
