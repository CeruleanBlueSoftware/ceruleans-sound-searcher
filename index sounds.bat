@echo off
cd /d "%~dp0"
set TRANSFORMERS_VERBOSITY=error
set HF_HUB_DISABLE_PROGRESS_BARS=1
".win\Scripts\python.exe" sound_index.py
echo.
echo The report is in "index report.txt". Press any key to close.
pause > nul
