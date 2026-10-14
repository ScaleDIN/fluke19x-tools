@echo off
rem Double-click: convert the 196B/196C module in the programmer to a 199C V8.04 (keeps its cal).
rem For a mono 196B with a color LCD fitted, run: convert_to_199c.bat --color-lcd
cd /d "%~dp0"
set FLUKE_PAUSE=1
python tools\convert_to_199c.py %*
