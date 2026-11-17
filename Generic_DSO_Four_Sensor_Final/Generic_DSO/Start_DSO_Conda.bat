@echo off
setlocal
cd /d "%~dp0"
title Generic DSO - portable-dso Conda environment
set "DSO_CONDA="
if defined CONDA_EXE if exist "%CONDA_EXE%" set "DSO_CONDA=%CONDA_EXE%"
if not defined DSO_CONDA for /f "delims=" %%I in ('where conda.exe 2^>nul') do if not defined DSO_CONDA set "DSO_CONDA=%%I"
if not defined DSO_CONDA for /f "delims=" %%I in ('where conda.bat 2^>nul') do if not defined DSO_CONDA set "DSO_CONDA=%%I"
for %%I in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%PROGRAMDATA%\anaconda3" "%PROGRAMDATA%\miniconda3") do (
    if not defined DSO_CONDA if exist "%%~I\Scripts\conda.exe" set "DSO_CONDA=%%~I\Scripts\conda.exe"
)
if not defined DSO_CONDA (
    echo Open Anaconda Prompt or your Conda PowerShell in this project folder.
    echo Then run .\Start_DSO_Conda.bat again.
    pause
    exit /b 1
)
call "%DSO_CONDA%" run -n portable-dso python --version >nul 2>&1
if errorlevel 1 (
    echo Create the environment from a Conda terminal with:
    echo conda create -n portable-dso python=3.13 pip
    pause
    exit /b 1
)
call "%DSO_CONDA%" run -n portable-dso python -c "import numpy, PySide6, pyqtgraph, serial" >nul 2>&1
if errorlevel 1 (
    echo Installing the Generic DSO desktop dependencies into portable-dso...
    call "%DSO_CONDA%" run -n portable-dso --no-capture-output python -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)
call "%DSO_CONDA%" run -n portable-dso --no-capture-output python scope_app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Generic DSO could not start. Review the error above.
pause
exit /b 1
