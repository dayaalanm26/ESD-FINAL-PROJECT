@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run Start_DSO.bat once before building.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install pyinstaller
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --name Generic_DSO --collect-all pyqtgraph Generic_DSO.pyw
if errorlevel 1 goto error
echo Built dist\Generic_DSO\Generic_DSO.exe
echo Keep the complete dist\Generic_DSO folder together when copying to another PC.
pause
exit /b 0
:error
echo Build failed. Review the error above.
pause
exit /b 1
