@echo off
cd /d "%~dp0"

REM Use the portable Python, the same way every other launcher does.
REM One Python for every launcher, looked for in this order:
REM   1. runtime\python        - what setup.bat installs, inside this checkout
REM   2. ..\python_portable    - the shared environment beside the checkout
set "PORTABLE_PYTHON=%~dp0runtime\python\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\python.exe"

if not exist "%PORTABLE_PYTHON%" (
    echo [ERROR] Could not start Slate Console: no Python was found. Looked in:
    echo         %~dp0runtime\python\python.exe
    echo         %~dp0..\python_portable\Scripts\python.exe
    echo         %~dp0..\python_portable\python.exe
    echo         Run setup.bat, or put the portable Python beside this folder.
    pause
    exit /b 1
)

echo [INFO] Using Portable Python Environment...
echo [INFO] Launching Slate Console...

"%PORTABLE_PYTHON%" tools/slate_console/main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] Slate Console exited with error code %ERRORLEVEL%
    pause
)
