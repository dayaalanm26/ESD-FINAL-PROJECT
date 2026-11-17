@echo off
call "%~dp0Start_DSO.bat" --build
exit /b %errorlevel%
