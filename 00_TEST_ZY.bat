@echo off
setlocal
cd /d "%~dp0"
zy --help
zy check examples\v2_language_tour.zy
zy run examples\v2_language_tour.zy
zy run examples\v2_file_tree.zy -- examples
pause
