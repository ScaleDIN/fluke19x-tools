@echo off
rem Double-click: enable Medical + BusHealth on a connected Fluke 19xC (V08.04).
cd /d "%~dp0"
set FLUKE_PAUSE=1
python tools\enable_all_options.py %*
