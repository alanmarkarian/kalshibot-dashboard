@echo off
cd /d "%~dp0"
title GRPN quick check
python check_position.py GRPN
echo.
pause
