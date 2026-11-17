@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto deps
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo Install Python 3.10 or newer with Tcl/Tk enabled, then run this file again.
    pause
    exit /b 1
)
:deps
".venv\Scripts\python.exe" -c "import serial" >nul 2>&1
if errorlevel 1 (
    echo Installing the USB serial dependency...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 echo USB serial setup failed. Demo and TCP mode can still open.
)
".venv\Scripts\python.exe" scope_app.py
if errorlevel 1 pause
