@echo off
cd /d "%~dp0.."
echo ========================================
echo   Slate Emergency Recovery Launcher
echo ========================================
echo.
echo Launching emergency updater...
REM One Python for every launcher, looked for in this order:
REM   1. runtime\python        - what setup.bat installs, inside this checkout
REM   2. ..\python_portable    - the shared environment beside the checkout
set "PORTABLE_PYTHON=%~dp0..\runtime\python\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\..\python_portable\python.exe"
if not exist "%PORTABLE_PYTHON%" (
    echo [ERROR] Portable Python not found.
    pause
    exit /b 1
)
"%PORTABLE_PYTHON%" tools\emergency_updater.py
pause
