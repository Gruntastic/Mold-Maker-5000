@echo off
setlocal
cd /d "%~dp0"
if exist "dist\Casting Mold Maker 9.exe" (
    start "" "dist\Casting Mold Maker 9.exe"
    exit /b
)
if not exist ".venv\Scripts\python.exe" (
    echo Set up the app using README.md, then open this file again.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" mold_maker.py
if errorlevel 1 pause
