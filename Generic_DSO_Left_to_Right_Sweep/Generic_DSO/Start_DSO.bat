@echo off
setlocal
cd /d "%~dp0"
title Generic DSO
set "DSO_PYTHON="
set "DSO_CONDA="

rem Prefer an existing generic_dso environment, including custom env locations.
if /I "%CONDA_DEFAULT_ENV%"=="generic_dso" if exist "%CONDA_PREFIX%\python.exe" set "DSO_PYTHON=%CONDA_PREFIX%\python.exe"
if not defined DSO_PYTHON if exist "%USERPROFILE%\.conda\environments.txt" (
    for /f "usebackq delims=" %%E in ("%USERPROFILE%\.conda\environments.txt") do if /I "%%~nxE"=="generic_dso" if exist "%%E\python.exe" set "DSO_PYTHON=%%E\python.exe"
)
for %%B in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3" "C:\ProgramData\anaconda3" "C:\ProgramData\miniconda3") do (
    if not defined DSO_PYTHON if exist "%%~B\envs\generic_dso\python.exe" set "DSO_PYTHON=%%~B\envs\generic_dso\python.exe"
)
if defined DSO_PYTHON goto packages

rem Fallback: reuse or create a project-local virtual environment.
if exist ".venv\Scripts\python.exe" goto venv_ready
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto error
:venv_ready
set "DSO_PYTHON=%~dp0.venv\Scripts\python.exe"
:packages
"%DSO_PYTHON%" -c "import numpy, PySide6, pyqtgraph, serial" >nul 2>nul
if errorlevel 1 (
    "%DSO_PYTHON%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 goto error
)
if /I "%~1"=="--build" goto build
"%DSO_PYTHON%" "%~dp0scope_app.py"
if errorlevel 1 goto error
exit /b 0
:build
"%DSO_PYTHON%" -m pip install pyinstaller
if errorlevel 1 goto error
"%DSO_PYTHON%" -m PyInstaller --noconfirm --clean --windowed --onedir --name Generic_DSO --collect-all pyqtgraph Generic_DSO.pyw
if errorlevel 1 goto error
echo Built dist\Generic_DSO\Generic_DSO.exe. Keep the entire folder together.
pause
exit /b 0
:error
echo.
echo Generic DSO could not start. Review the error above.
echo Install Python 3.10 or newer with Python added to PATH if Python is missing.
echo First-time dependency installation needs an internet connection.
pause
exit /b 1
