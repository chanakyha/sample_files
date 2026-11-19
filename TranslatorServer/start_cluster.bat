@echo off
rem Starts the Translator API scaled out: several API instances behind a load balancer.
rem Usage: start_cluster.bat [instances]      (default 2; load balancer on port 8000)
rem Set TRANSLATOR_HOST=0.0.0.0 to accept connections from other machines.
setlocal
cd /d "%~dp0"
set INSTANCES=%1
if "%INSTANCES%"=="" set INSTANCES=2
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Create it first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)
echo Translator cluster: %INSTANCES% instance(s) behind http://127.0.0.1:8000  - press Ctrl+C to stop
echo Logs: %~dp0logs\
".venv\Scripts\python.exe" cluster.py --instances %INSTANCES% --port 8000
