@echo off
rem Starts the offline Translator API (separate from the desktop app).
rem Usage: start_api.bat [port]      (default port 8000; host 127.0.0.1)
rem Set TRANSLATOR_HOST=0.0.0.0 to accept connections from other machines.
setlocal
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" set PORT=8000
if "%TRANSLATOR_HOST%"=="" set TRANSLATOR_HOST=127.0.0.1
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Create it first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)
echo Translator API on http://%TRANSLATOR_HOST%:%PORT%  ^(docs: /docs^)  - press Ctrl+C to stop
".venv\Scripts\python.exe" -m uvicorn app:app --host %TRANSLATOR_HOST% --port %PORT%
