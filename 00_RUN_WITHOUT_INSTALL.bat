@echo off
setlocal
cd /d "%~dp0"
python zyen.py run tour --project examples
pause
