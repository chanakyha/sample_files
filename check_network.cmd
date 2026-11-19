@echo off
rem Checks whether the sites needed to set up, run and retrain the translator can be reached
rem from this network (for example a corporate Wi-Fi that blocks some sites).
rem
rem Usage:
rem   check_network.cmd                                   direct connection (or HTTPS_PROXY if set)
rem   check_network.cmd --proxy http://proxy.corp:8080    through a proxy
rem   check_network.cmd --proxy http://user:pass@proxy.corp:8080
rem
rem Exit code: number of required sites that could not be reached (0 = everything needed works).
setlocal

set "PROXY="
:args
if "%~1"=="" goto args_done
if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
if "%~1"=="/?" goto usage
if /i "%~1"=="--proxy" goto arg_proxy
if /i "%~1"=="-p" goto arg_proxy
echo Unknown option: %~1
echo.
goto usage
:arg_proxy
if "%~2"=="" (
    echo --proxy needs a value, e.g. --proxy http://proxy.corp:8080
    exit /b 99
)
set "PROXY=%~2"
shift
shift
goto args
:args_done

set "CURL=%SystemRoot%\System32\curl.exe"
if exist "%CURL%" goto curl_ok
set "CURL="
for /f "delims=" %%c in ('where curl 2^>nul') do if not defined CURL set "CURL=%%c"
if defined CURL goto curl_ok
echo curl.exe was not found. It is part of Windows 10 1803 and later.
exit /b 99
:curl_ok

set "TMPF=%TEMP%\check_network_%RANDOM%.txt"
set /a REQ_FAIL=0
set /a REQ_TOTAL=0

echo Translator network check
echo ========================
if defined PROXY (
    echo Proxy:          %PROXY%
    set "PXARG=--proxy %PROXY%"
) else (
    set "PXARG="
    if defined HTTPS_PROXY (
        echo Proxy:          %HTTPS_PROXY%  [from the HTTPS_PROXY environment variable]
    ) else (
        echo Proxy:          none, direct connection
    )
)
call :show_windows_proxy
echo.

echo Needed to set up and run
call :check req "github.com" "https://github.com/" "git clone and push"
call :check req "pypi.org" "https://pypi.org/simple/fastapi/" "pip install: package index"
call :check req "files.pythonhosted.org" "https://files.pythonhosted.org/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl" "pip install: package downloads"
call :check req "builds.dotnet.microsoft.com" "https://builds.dotnet.microsoft.com/dotnet/Sdk/5.0.408/dotnet-sdk-5.0.408-win-x64.zip" "setup: .NET 5 SDK download"
echo.
echo Needed to retrain the models (download_data.cmd)
call :check req "raw.githubusercontent.com" "https://raw.githubusercontent.com/Helsinki-NLP/Tatoeba-Challenge/master/data/dev/eng-fra/dev.txt" "Tatoeba and treebank files"
call :check req "codeload.github.com" "https://codeload.github.com/UDS-SFB-B6-Datasets/Multilingual-Parallel-Direct-Europarl/zip/refs/heads/main" "Europarl and treebank zip files"
echo.
echo Optional
call :check opt "api.nuget.org" "https://api.nuget.org/v3/index.json" "standalone app build: .NET 5 runtime packs"
call :check opt "download.pytorch.org" "https://download.pytorch.org/whl/cu126/torch/" "PyTorch with CUDA, GPU training only"
call :check opt "www.python.org" "https://www.python.org/downloads/" "Python installer"
call :check opt "dotnet.microsoft.com" "https://dotnet.microsoft.com/download/dotnet/5.0" ".NET download pages"
echo.
echo Not used any more, shown for information
call :check info "opus.nlpl.eu" "https://opus.nlpl.eu/" "old dataset site"
call :check info "object.pouta.csc.fi" "https://object.pouta.csc.fi/" "old dataset downloads"
call :check info "www.kaggle.com" "https://www.kaggle.com/" "Kaggle"
del "%TMPF%" 2>nul
echo.

if %REQ_FAIL%==0 goto all_ok
echo RESULT: %REQ_FAIL% of %REQ_TOTAL% required sites cannot be reached.
echo.
if defined PROXY goto fail_with_proxy
echo If your company uses a proxy, run this again with it, e.g.
echo     check_network.cmd --proxy http://proxy.yourcompany.com:8080
echo The Windows proxy setting shown above is usually the right one. If only a PAC script is
echo set, ask IT for the proxy address, or open the PAC address in a browser to read it.
echo If a proxy does not help, ask IT to allow the sites marked BLOCKED, or do the steps that
echo need them on another network. See docs\SETUP_GUIDE.md.
exit /b %REQ_FAIL%
:fail_with_proxy
echo Check the proxy address and port. If the proxy needs a login, use
echo     check_network.cmd --proxy http://user:password@proxy.yourcompany.com:8080
echo If it still fails, ask IT to allow the sites marked BLOCKED. See docs\SETUP_GUIDE.md.
exit /b %REQ_FAIL%

:all_ok
echo RESULT: all %REQ_TOTAL% required sites can be reached.
if not defined PROXY exit /b 0
echo.
echo The proxy works. Use it for the setup commands in the same window:
echo.
echo   cmd.exe:      set HTTPS_PROXY=%PROXY%
echo                 set HTTP_PROXY=%PROXY%
echo   PowerShell:   $env:HTTPS_PROXY = "%PROXY%"
echo                 $env:HTTP_PROXY = "%PROXY%"
echo.
echo pip, git, dotnet and TranslatorTraining\download_data.py all use these variables. Or pass it directly:
echo   setup.cmd --proxy %PROXY%
echo   download_data.cmd --proxy %PROXY%
echo   git config --global http.proxy %PROXY%
exit /b 0

rem ---------------------------------------------------------------------------------------
rem :check  group  name  url  purpose
rem   group: req (counted in the result), opt, info (any HTTP answer counts as reachable)
:check
set "NAME=%~2                              "
set "NAME=%NAME:~0,27%"
if /i "%~1"=="req" set /a REQ_TOTAL+=1
"%CURL%" -s -I -o NUL --connect-timeout 10 --max-time 25 %PXARG% -w "%%{http_code} %%{http_connect}" "%~3" > "%TMPF%" 2>nul
set "RC=%errorlevel%"
set "HTTP=000"
set "CONN=000"
for /f "usebackq tokens=1,2" %%a in ("%TMPF%") do (
    set "HTTP=%%a"
    set "CONN=%%b"
)
set "H1=%HTTP:~0,1%"
if not "%RC%"=="0" goto check_error
if /i "%~1"=="info" goto check_ok
if "%H1%"=="2" goto check_ok
if "%H1%"=="3" goto check_ok
if "%HTTP%"=="407" (set "WHY=the proxy needs a user name and password") else if "%HTTP%"=="403" (set "WHY=HTTP 403 forbidden, probably the corporate filter") else (set "WHY=unexpected answer HTTP %HTTP%")
goto check_failed

:check_error
set "WHY=curl error %RC%"
if "%RC%"=="5" set "WHY=proxy host name not found"
if "%RC%"=="6" set "WHY=site name not found, DNS blocked or no internet"
if "%RC%"=="7" set "WHY=connection refused or blocked by the firewall"
if "%RC%"=="28" set "WHY=no answer, timed out: blocked or very slow"
if "%RC%"=="35" set "WHY=secure connection failed, blocked or HTTPS inspection"
if "%RC%"=="52" set "WHY=connection closed without an answer, blocked"
if "%RC%"=="56" set "WHY=connection reset, blocked"
if "%RC%"=="60" set "WHY=certificate not trusted, corporate HTTPS inspection"
if "%CONN%"=="403" set "WHY=the proxy refused this site, blocked"
if "%CONN%"=="407" set "WHY=the proxy needs a user name and password"

:check_failed
if /i "%~1"=="req" (
    set /a REQ_FAIL+=1
    echo   BLOCKED   %NAME% %~4: %WHY%
) else if /i "%~1"=="opt" (
    echo   BLOCKED   %NAME% %~4: %WHY%
) else (
    echo   blocked   %NAME% %~4: %WHY%
)
exit /b 0

:check_ok
echo   OK        %NAME% %~4
exit /b 0

rem ---------------------------------------------------------------------------------------
:show_windows_proxy
set "IE=HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
set "PENABLE="
set "PSERVER="
set "PPAC="
for /f "tokens=3" %%a in ('reg query "%IE%" /v ProxyEnable 2^>nul ^| find "ProxyEnable"') do set "PENABLE=%%a"
for /f "tokens=2,*" %%a in ('reg query "%IE%" /v ProxyServer 2^>nul ^| find "ProxyServer"') do set "PSERVER=%%b"
for /f "tokens=2,*" %%a in ('reg query "%IE%" /v AutoConfigURL 2^>nul ^| find "AutoConfigURL"') do set "PPAC=%%b"
if "%PENABLE%"=="0x1" if defined PSERVER echo Windows proxy:  %PSERVER%
if not "%PENABLE%"=="0x1" echo Windows proxy:  none set
if defined PPAC echo Windows PAC:    %PPAC%
exit /b 0

:usage
echo Checks whether the sites the translator setup needs can be reached from this network.
echo.
echo   check_network.cmd                                  test a direct connection
echo   check_network.cmd --proxy http://host:port         test through a proxy
echo   check_network.cmd --proxy http://user:pass@host:port
echo.
echo If HTTPS_PROXY is set and --proxy is not given, curl uses HTTPS_PROXY.
echo Exit code: number of required sites that cannot be reached; 0 means all OK.
exit /b 0
