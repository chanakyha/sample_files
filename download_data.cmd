@echo off
rem Downloads the training data from GitHub (about 1.1 GB) and converts it into
rem TranslatorService\training\data\raw\. Only needed to retrain the models: the trained
rem models are already in the repository.
rem
rem Usage:
rem   download_data.cmd
rem   download_data.cmd --proxy http://proxy.yourcompany.com:8080
rem   download_data.cmd --proxy http://user:password@proxy.yourcompany.com:8080
rem   download_data.cmd --list          (only print the download addresses)
rem
rem Files already downloaded are kept, so running it again continues where it stopped.
setlocal
cd /d "%~dp0TranslatorService"

if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
if "%~1"=="/?" goto usage

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo The Python environment is missing. Run setup.cmd first.
    exit /b 1
)
"%PY%" -c "import truststore" 2>nul
if errorlevel 1 (
    echo Note: truststore is not installed, so HTTPS certificates are checked without the
    echo Windows certificate store. Behind a proxy that inspects HTTPS this can fail; then run
    echo   setup.cmd --training
    echo.
)

echo Downloading the training data from GitHub into TranslatorService\training\data\raw\
echo (about 1.1 GB; then converting, about 10 minutes in total)
echo.
"%PY%" -X utf8 training\download_data.py %*
if errorlevel 1 (
    echo.
    echo DOWNLOAD FAILED. Run it again to retry; finished files are kept.
    echo To see which sites are blocked: check_network.cmd  [--proxy http://host:port]
    exit /b 1
)
echo.
echo Done. Next steps to retrain, from TranslatorService ^(see docs\MODEL_TRAINING.md^):
echo   .venv\Scripts\python training\prepare_data.py
echo   .venv\Scripts\python training\train_lid.py
echo   .venv\Scripts\python training\train_nmt.py --hours 10
exit /b 0

:usage
echo Downloads the training data from GitHub into TranslatorService\training\data\raw\.
echo.
echo   download_data.cmd                                  direct connection, or HTTPS_PROXY
echo   download_data.cmd --proxy http://host:port         through a proxy
echo   download_data.cmd --proxy http://user:pass@host:port
echo   download_data.cmd --list                           print the download addresses
exit /b 0
