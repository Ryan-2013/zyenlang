@echo off
setlocal
cd /d "%~dp0"
python zyen.py run examples\v2_language_tour.zy
pause
