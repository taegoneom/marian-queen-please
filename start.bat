@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -B main.py --auto
if errorlevel 1 pause
