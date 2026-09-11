@echo off
set "PORTABLE_PYTHON=%~dp0..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\python.exe"

if not exist "%PORTABLE_PYTHON%" (
    echo [ERROR] Portable Python not found at %PORTABLE_PYTHON%
    pause
    exit /b 1
)

echo [INFO] Using Portable Python Environment...
echo [INFO] Launching UT Studio Operations...

"%PORTABLE_PYTHON%" ut_vfx/studio_ops_main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] UT Studio Operations exited with error code %ERRORLEVEL%
    pause
)
