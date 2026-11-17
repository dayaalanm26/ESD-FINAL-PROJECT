@echo off
setlocal
cd /d "%~dp0"
if not exist "scope_app.py" (
    echo Copy these files into the existing DSO folder containing scope_app.py.
    pause
    exit /b 1
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" sensor_scope.py
) else (
    python sensor_scope.py
)
if errorlevel 1 (
    echo Run Start_DSO.bat once to install the original DSO dependencies.
    pause
    exit /b 1
)
