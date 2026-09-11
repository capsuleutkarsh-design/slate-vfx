@echo off
set "PORTABLE_PYTHON=%~dp0..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\python.exe"

if not exist "%PORTABLE_PYTHON%" (
    echo [ERROR] Portable Python not found at %PORTABLE_PYTHON%
    pause
    exit /b 1
)

echo [INFO] Using Portable Python Environment...
echo [INFO] Launching Slate Operations...

"%PORTABLE_PYTHON%" slate/studio_ops_main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] Slate Operations exited with error code %ERRORLEVEL%
    pause
)
