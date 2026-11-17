@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scope_app.py --demo
    if errorlevel 1 pause
    exit /b
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 scope_app.py --demo
) else (
    python scope_app.py --demo
)
if errorlevel 1 pause
