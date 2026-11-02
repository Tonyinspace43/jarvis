@echo off
title Jarvis Launcher
cd /d "C:\Users\Tonny\jarvis"

rem ---- Change this if you start OmniRoute with a different command ----
set OMNIROUTE_CMD=omniroute

rem Give Wi-Fi a moment to connect after login
timeout /t 10 /nobreak >nul

rem Start OmniRoute only if it is not already running
call :check
if not errorlevel 1 goto run
start "OmniRoute" /min cmd /k %OMNIROUTE_CMD%
set tries=0

:wait
timeout /t 2 /nobreak >nul
call :check
if not errorlevel 1 goto run
set /a tries+=1
if %tries% lss 20 goto wait
echo OmniRoute did not respond. Starting Jarvis anyway.

:run
"C:\Users\Tonny\jarvis\venv\Scripts\python.exe" jarvis.py
pause
exit /b

:check
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 20128); $c.Close(); exit 0 } catch { exit 1 }"
exit /b %errorlevel%