@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title Short Squeeze Screener

echo.
echo  ============================================
echo   Short Squeeze Screener
echo  ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo Python not found on PATH. Install from https://www.python.org/downloads/
  echo and CHECK "Add Python to PATH" during setup.
  pause
  exit /b 1
)

if not exist ".deps_ok" (
  echo First run: installing dependencies...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo pip install failed.
    pause
    exit /b 1
  )
  echo. > .deps_ok
)

echo Scanning... this can take 1-3 minutes depending on universe size.
echo.
python scan.py %*
if errorlevel 1 (
  echo.
  echo Scan failed. See errors above.
  pause
  exit /b 1
)

echo.
echo Done. Dashboard: dashboard.html
pause
