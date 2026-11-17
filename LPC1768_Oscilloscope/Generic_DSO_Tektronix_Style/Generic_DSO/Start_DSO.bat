@echo off
setlocal
cd /d "%~dp0"
title Generic DSO - Setup and Launch
if exist ".venv\Scripts\python.exe" goto verify
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto pythonerror
:verify
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if errorlevel 1 goto pythonerror
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3,10); import PySide6, pyqtgraph, numpy, serial" >nul 2>nul
if not errorlevel 1 goto launch
echo Installing DSO dependencies. Internet is required for this first setup.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto installerror
:launch
start "Generic DSO" ".venv\Scripts\pythonw.exe" "%~dp0Generic_DSO.pyw"
exit /b 0
:pythonerror
echo Install Python 3.10 or newer for Windows with pip and the Python launcher.
echo Then double-click Start_DSO.bat again.
pause
exit /b 1
:installerror
echo Setup failed. Check your internet connection and Python installation.
pause
exit /b 1
