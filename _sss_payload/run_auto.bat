@echo off
REM Unattended scan for Task Scheduler — no pause, no browser.
REM Usage: run_auto.bat
REM        run_auto.bat --quick
setlocal
REM Force UTF-8 so Task Scheduler cp1252 cannot kill prints on arrows/glyphs
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"

if not exist "data\logs" mkdir "data\logs"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set STAMP=%%i
set LOG=data\logs\scan_%STAMP%.log

echo [%DATE% %TIME%] Short Squeeze Screener auto-run starting >> "%LOG%"
echo Working dir: %CD% >> "%LOG%"

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: python not on PATH >> "%LOG%"
  exit /b 1
)

REM Ensure deps once (same marker as run.bat)
if not exist ".deps_ok" (
  echo Installing dependencies... >> "%LOG%"
  python -m pip install -r requirements.txt >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo pip install failed >> "%LOG%"
    exit /b 1
  )
  echo. > .deps_ok
)

echo Running: python scan.py --no-browser %* >> "%LOG%"
python scan.py --no-browser %* >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%

echo [%DATE% %TIME%] finished exit=%RC% >> "%LOG%"

REM Keep a pointer to the latest log
echo %LOG%> data\logs\latest.txt

REM Prune logs older than 30 days (best-effort)
powershell -NoProfile -Command "Get-ChildItem -Path 'data\logs\scan_*.log' -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force -ErrorAction SilentlyContinue"

exit /b %RC%
