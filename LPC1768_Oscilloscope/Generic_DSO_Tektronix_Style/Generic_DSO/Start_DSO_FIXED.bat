@echo off
setlocal
cd /d "%~dp0"
title Generic DSO

rem ------------------------------------------------------------
rem Use the existing Conda environment: generic_dso
rem ------------------------------------------------------------
set "PYW="

if exist "%USERPROFILE%\anaconda3\envs\generic_dso\pythonw.exe" set "PYW=%USERPROFILE%\anaconda3\envs\generic_dso\pythonw.exe"
if not defined PYW if exist "%USERPROFILE%\miniconda3\envs\generic_dso\pythonw.exe" set "PYW=%USERPROFILE%\miniconda3\envs\generic_dso\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\anaconda3\envs\generic_dso\pythonw.exe" set "PYW=%LOCALAPPDATA%\anaconda3\envs\generic_dso\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\miniconda3\envs\generic_dso\pythonw.exe" set "PYW=%LOCALAPPDATA%\miniconda3\envs\generic_dso\pythonw.exe"
if not defined PYW if exist "C:\ProgramData\anaconda3\envs\generic_dso\pythonw.exe" set "PYW=C:\ProgramData\anaconda3\envs\generic_dso\pythonw.exe"
if not defined PYW if exist "C:\ProgramData\miniconda3\envs\generic_dso\pythonw.exe" set "PYW=C:\ProgramData\miniconda3\envs\generic_dso\pythonw.exe"

if not defined PYW goto noenv

rem Verify required packages before starting.
"%PYW%" -c "import PySide6, pyqtgraph, numpy, serial" >nul 2>nul
if errorlevel 1 goto pkgerror

rem Launch directly from this folder. No simulation and no venv setup.
start "Generic DSO" "%PYW%" "%~dp0Generic_DSO.pyw"
exit /b 0

:noenv
echo.
echo ============================================================
echo Generic DSO could not find the Conda environment generic_dso.
echo ============================================================
echo.
echo Open Anaconda Prompt, activate generic_dso, then run:
echo     where python
echo.
echo Send the displayed path if you need the launcher adjusted.
echo.
pause
exit /b 1

:pkgerror
echo.
echo ============================================================
echo The generic_dso environment was found, but required packages
echo are missing.
echo ============================================================
echo.
echo Open Anaconda Prompt and run:
echo     conda activate generic_dso
echo     python -m pip install -r "%~dp0requirements.txt"
echo.
pause
exit /b 1
