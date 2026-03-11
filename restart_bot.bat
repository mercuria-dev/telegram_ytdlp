@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Auto-restart runner for telegram_ytdlp bot (Windows).
REM Creates per-run logs and records exit codes.

cd /d "%~dp0"

if not exist "logs" mkdir "logs" >nul 2>&1
if not exist "logs\runs" mkdir "logs\runs" >nul 2>&1

set "RESTART_LOG=logs\restart.log"

REM Unbuffered output so logs contain last lines before crash.
set "PYTHONUNBUFFERED=1"

:loop
call :timestamp TS
set "RUN_LOG=logs\runs\bot_!TS!.log"

echo [!TS!] starting bot; log=!RUN_LOG!>>"%RESTART_LOG%"
echo [!TS!] starting bot; log=!RUN_LOG!

if exist "venv\Scripts\activate.bat" (
  call "venv\Scripts\activate.bat" >nul 2>&1
)

python -u main.py >>"!RUN_LOG!" 2>&1
set "RC=%ERRORLEVEL%"

call :timestamp TS2

REM Common Windows crash/termination codes (NTSTATUS) that bubble as exit codes.
REM 3221225786 = 0xC000013A (terminated by Ctrl+C / console close)
REM 3221225477 = 0xC0000005 (access violation)

if "!RC!"=="3221225786" (
  echo [!TS2!] bot exited rc=!RC! (0xC000013A: terminated by Ctrl+C/close)>>"%RESTART_LOG%"
  echo [!TS2!] bot exited rc=!RC! (0xC000013A: terminated by Ctrl+C/close)
) else if "!RC!"=="3221225477" (
  echo [!TS2!] bot exited rc=!RC! (0xC0000005: access violation / native crash)>>"%RESTART_LOG%"
  echo [!TS2!] bot exited rc=!RC! (0xC0000005: access violation / native crash)
) else if "!RC!"=="137" (
  echo [!TS2!] bot exited rc=!RC! (often treated as SIGKILL in Linux; on Windows usually external kill)>>"%RESTART_LOG%"
  echo [!TS2!] bot exited rc=!RC! (often treated as SIGKILL in Linux; on Windows usually external kill)
) else (
  echo [!TS2!] bot exited rc=!RC!>>"%RESTART_LOG%"
  echo [!TS2!] bot exited rc=!RC!
)

REM Small backoff to avoid hot-looping.
timeout /t 2 /nobreak >nul
goto loop

:timestamp
set "%~1="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'"`) do set "%~1=%%I"
exit /b 0
