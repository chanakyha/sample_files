@echo off
rem Starts the bundled Python server (TranslatorServer.exe, built by build_server_exe.cmd).
rem
rem Usage:
rem   run_server.cmd                          cluster: 2 instances + load balancer on port 8000
rem   run_server.cmd --instances 4
rem   run_server.cmd --instances 4 --host 0.0.0.0     reachable from other machines
rem   run_server.cmd --single                 one instance, no load balancer
rem   run_server.cmd --help
rem
rem Ctrl+C stops it. Logs: TranslatorServer\dist\TranslatorServer\logs\
setlocal
set "EXE=%~dp0TranslatorServer\dist\TranslatorServer\TranslatorServer.exe"
if not exist "%EXE%" (
    echo TranslatorServer.exe has not been built yet. Run build_server_exe.cmd first,
    echo or start the server from source: TranslatorServer\start_cluster.bat
    exit /b 1
)
"%EXE%" %*
