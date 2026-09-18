@echo off
setlocal
cd /d "%~dp0"
zy --help
zy check tour --project examples
zy run tour --project examples
zy run file-tree --project examples -- examples
pause
