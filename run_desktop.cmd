@echo off
rem Starts the desktop app. If setup installed the .NET 5 SDK in .dotnet\, the app runs on its
rem .NET 5.0.17 runtime; otherwise on the .NET Desktop Runtime installed on the PC (5 or newer).
rem Arguments are passed on, e.g.  run_desktop.cmd --url http://server01:8000
setlocal
cd /d "%~dp0"
set "EXE=%~dp0TranslatorDesktop\bin\Debug\net5.0-windows\TranslatorDesktop.exe"
if not exist "%EXE%" (
    echo The desktop app is not built yet. Run setup.cmd first.
    exit /b 1
)
if exist "%~dp0.dotnet\shared\Microsoft.WindowsDesktop.App\5.0.17" (
    set "DOTNET_ROOT=%~dp0.dotnet"
    set "DOTNET_MULTILEVEL_LOOKUP=0"
)
start "" "%EXE%" %*
