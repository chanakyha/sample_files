@echo off
rem Builds the bundled Python server: TranslatorServer\dist\TranslatorServer\TranslatorServer.exe
rem (PyInstaller one-folder build with Python, PyTorch, FastAPI and the models; nothing to install
rem on the target PC). Copy the whole dist\TranslatorServer folder to run it elsewhere.
rem
rem Usage:
rem   build_server_exe.cmd
rem   build_server_exe.cmd --proxy http://proxy.yourcompany.com:8080   (pip download of PyInstaller)
rem
rem Needs TranslatorServer\.venv (run setup.cmd first).
setlocal
set "HERE=%~dp0"
set "PROXY="
:args
if "%~1"=="" goto args_done
if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
if "%~1"=="/?" goto usage
if /i "%~1"=="--proxy" (
    if "%~2"=="" (
        echo --proxy needs a value, e.g. --proxy http://proxy.corp:8080
        exit /b 1
    )
    set "PROXY=%~2"
    shift
    shift
    goto args
)
echo Unknown option: %~1
echo.
goto usage
:args_done

cd /d "%HERE%TranslatorServer"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo The Python environment TranslatorServer\.venv is missing. Run setup.cmd first.
    exit /b 1
)
if not exist "models\nmt-en-es-fr\model.pt" (
    echo The models are missing in TranslatorServer\models\.
    exit /b 1
)
tasklist /fi "imagename eq TranslatorServer.exe" 2>nul | find /i "TranslatorServer.exe" >nul
if not errorlevel 1 (
    echo TranslatorServer.exe is running. Stop it first ^(Ctrl+C in its window^).
    exit /b 1
)

echo [1/3] PyInstaller
set "PIPPROXY="
if defined PROXY set "PIPPROXY=--proxy %PROXY%"
"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    "%PY%" -m pip install --disable-pip-version-check "pyinstaller>=6.16" %PIPPROXY%
    if errorlevel 1 (
        echo Installing PyInstaller failed. Check the network: check_network.cmd [--proxy URL]
        exit /b 1
    )
)

echo [2/3] Building the exe (a few minutes)
"%PY%" -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build TranslatorServer.spec
if errorlevel 1 (
    echo BUILD FAILED.
    exit /b 1
)

echo [3/3] Copying the models
robocopy models dist\TranslatorServer\models /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo Copying the models failed.
    exit /b 1
)

echo.
echo Done: %CD%\dist\TranslatorServer\TranslatorServer.exe
echo.
echo   run_server.cmd                       start it (cluster, 2 instances, port 8000)
echo   run_server.cmd --instances 4
echo   dist\TranslatorServer\TranslatorServer.exe --single
echo.
echo To use it on another PC, copy the whole folder dist\TranslatorServer.
exit /b 0

:usage
echo Builds TranslatorServer\dist\TranslatorServer\TranslatorServer.exe, the Python server as an exe.
echo.
echo   build_server_exe.cmd
echo   build_server_exe.cmd --proxy http://host:port    proxy for downloading PyInstaller
exit /b 0
