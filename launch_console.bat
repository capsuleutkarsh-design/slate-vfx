@echo off
cd /d "%~dp0"

REM Use the portable Python, the same way every other launcher does.
REM This previously ran through Poetry, which is not installed alongside the
REM portable environment, so the console would not start on a machine that
REM only has the shipped runtime.
set "PORTABLE_PYTHON=%~dp0..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\python.exe"

if not exist "%PORTABLE_PYTHON%" (
    echo [WARN] Portable Python not found, trying Poetry instead...
    poetry run python tools/capsule_console/main.py
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo [ERROR] Could not start Capsule Console.
        echo         Expected portable Python at: %~dp0..\python_portable
        pause
    )
    exit /b %ERRORLEVEL%
)

echo [INFO] Using Portable Python Environment...
echo [INFO] Launching Capsule Console...

"%PORTABLE_PYTHON%" tools/capsule_console/main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] Capsule Console exited with error code %ERRORLEVEL%
    pause
)
