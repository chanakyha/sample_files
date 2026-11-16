@echo off
rem Sets up the EU Translator: Python environment for the API, .NET 5 SDK and desktop app build.
rem Runs setup.ps1. Options can be written --option or -Option:
rem   setup.cmd
rem   setup.cmd --proxy http://proxy.yourcompany.com:8080
rem   setup.cmd --proxy http://user:password@proxy.yourcompany.com:8080
rem   setup.cmd --training --gpu
rem   setup.cmd --wheels D:\wheels
rem All options: setup.cmd --help
setlocal
rem Save this script's folder now: after "shift", %~dp0 no longer refers to it
set "HERE=%~dp0"
set "ARGS="
:args
if "%~1"=="" goto run
set "A=%~1"
if /i "%A%"=="--help" goto help
if /i "%A%"=="-h" goto help
if "%A%"=="/?" goto help
rem --option -> -Option (PowerShell parameter syntax)
if "%A:~0,2%"=="--" set "A=%A:~1%"
set ARGS=%ARGS% "%A%"
shift
goto args

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%setup.ps1" %ARGS%
exit /b %errorlevel%

:help
echo setup.cmd [options]
echo.
echo   --proxy URL                    proxy for all downloads, e.g. http://proxy.corp:8080
echo                                  or http://user:password@proxy.corp:8080
echo   --proxyusedefaultcredentials   sign in to the proxy with your Windows account
echo   --training                     also install the packages for retraining
echo   --gpu                          also create .venv-gpu with CUDA PyTorch for GPU training
echo   --wheels FOLDER                install Python packages from wheel files, offline
echo   --runtests                     run the Python tests at the end
echo   --recreate                     recreate TranslatorService\.venv
echo   --skippython / --skipdotnet    skip that part
exit /b 0
