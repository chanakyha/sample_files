@echo off
rem Builds the desktop app as a standalone exe together with the bundled Python server:
rem
rem   publish\TranslatorDesktop\TranslatorDesktop.exe                    the app (.NET 5 runtime included)
rem   publish\TranslatorDesktop\TranslatorServer\TranslatorServer.exe    the API server (Python included)
rem
rem When the app starts and no API is running, it starts TranslatorServer.exe as a hidden
rem background process and stops it when the app closes. Copy the whole publish\TranslatorDesktop
rem folder to run it on another Windows 10/11 x64 PC; nothing needs to be installed there.
rem
rem Usage:
rem   build_desktop_exe.cmd
rem   build_desktop_exe.cmd --rebuildserver                  also rebuild TranslatorServer.exe
rem   build_desktop_exe.cmd --proxy http://proxy.corp:8080    proxy for NuGet / pip downloads
rem
rem Needs setup.cmd to have run first (.NET 5 SDK in .dotnet\, TranslatorServer\.venv).
setlocal
set "HERE=%~dp0"
set "PROXY="
set "REBUILD="
:args
if "%~1"=="" goto args_done
if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
if "%~1"=="/?" goto usage
if /i "%~1"=="--rebuildserver" (
    set "REBUILD=1"
    shift
    goto args
)
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

cd /d "%HERE%"
set "OUT=%HERE%publish\TranslatorDesktop"
set "SERVER_DIST=%HERE%TranslatorServer\dist\TranslatorServer"
set "DOTNET=%HERE%.dotnet\dotnet.exe"
if not exist "%DOTNET%" (
    echo The .NET 5 SDK is missing in .dotnet\. Run setup.cmd first.
    exit /b 1
)
tasklist /fi "imagename eq TranslatorDesktop.exe" 2>nul | find /i "TranslatorDesktop.exe" >nul
if not errorlevel 1 (
    echo TranslatorDesktop.exe is running. Close it first.
    exit /b 1
)
if defined PROXY (
    set "HTTPS_PROXY=%PROXY%"
    set "HTTP_PROXY=%PROXY%"
)

echo [1/3] Python server exe
set "NEED_SERVER="
if defined REBUILD set "NEED_SERVER=1"
if not exist "%SERVER_DIST%\TranslatorServer.exe" set "NEED_SERVER=1"
if defined NEED_SERVER (
    if defined PROXY (
        call "%HERE%build_server_exe.cmd" --proxy "%PROXY%"
    ) else (
        call "%HERE%build_server_exe.cmd"
    )
    if errorlevel 1 (
        echo Building TranslatorServer.exe failed.
        exit /b 1
    )
) else (
    echo   using the existing %SERVER_DIST%\TranslatorServer.exe  ^(--rebuildserver to rebuild it^)
)

echo [2/3] Desktop app (.NET 5, self-contained)
set "DOTNET_ROOT=%HERE%.dotnet"
set "DOTNET_MULTILEVEL_LOOKUP=0"
set "DOTNET_CLI_TELEMETRY_OPTOUT=1"
set "DOTNET_NOLOGO=1"
"%DOTNET%" publish TranslatorDesktop -c Release -r win-x64 --self-contained true -o "%OUT%" -v minimal
if errorlevel 1 (
    echo Building the desktop app failed. The first build needs api.nuget.org ^(check_network.cmd^).
    exit /b 1
)

echo [3/3] Copying the server next to the app
robocopy "%SERVER_DIST%" "%OUT%\TranslatorServer" /MIR /XD logs /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo Copying the server failed.
    exit /b 1
)

echo.
echo Done: %OUT%\TranslatorDesktop.exe
echo.
echo Start it with:  publish\TranslatorDesktop\TranslatorDesktop.exe
echo It starts TranslatorServer.exe in the background when no API is running at
echo http://127.0.0.1:8000 ^(or the --url address^), and stops it when it closes.
echo To use it on another PC, copy the whole folder publish\TranslatorDesktop.
exit /b 0

:usage
echo Builds publish\TranslatorDesktop\TranslatorDesktop.exe with the server TranslatorServer.exe next to it.
echo.
echo   build_desktop_exe.cmd
echo   build_desktop_exe.cmd --rebuildserver             also rebuild the Python server exe
echo   build_desktop_exe.cmd --proxy http://host:port    proxy for NuGet / pip
exit /b 0
