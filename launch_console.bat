@echo off
cd /d "%~dp0"

REM Use the portable Python, the same way every other launcher does.
REM This previously ran through Poetry, which is not installed alongside the
REM portable environment, so the console would not start on a machine that
REM only has the shipped runtime.
REM One Python for every launcher, looked for in this order:
REM   1. runtime\python        - what setup.bat installs, inside this checkout
REM   2. ..\python_portable    - the shared environment beside the checkout
set "PORTABLE_PYTHON=%~dp0runtime\python\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\python.exe"

if not exist "%PORTABLE_PYTHON%" (
    echo [WARN] Portable Python not found, trying Poetry instead...
    poetry run python tools/slate_console/main.py
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo [ERROR] Could not start Slate Console.
        echo         Expected portable Python at: %~dp0..\python_portable
        pause
    )
    exit /b %ERRORLEVEL%
)

echo [INFO] Using Portable Python Environment...
echo [INFO] Launching Slate Console...

"%PORTABLE_PYTHON%" tools/slate_console/main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] Slate Console exited with error code %ERRORLEVEL%
    pause
)
