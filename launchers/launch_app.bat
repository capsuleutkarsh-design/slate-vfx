@echo off
cd /d "%~dp0.."
REM One Python for every launcher, looked for in this order:
REM   1. runtime\python        - what setup.bat installs, inside this checkout
REM   2. ..\python_portable    - the shared environment beside the checkout
set "PORTABLE_PYTHON=%~dp0..\runtime\python\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\..\python_portable\python.exe"

if not exist "%PORTABLE_PYTHON%" (
    echo [ERROR] Portable Python not found at %PORTABLE_PYTHON%
    pause
    exit /b 1
)

echo [INFO] Using Portable Python Environment...
echo [INFO] Launching Slate Gatekeeper...

REM EXR and OpenImageIO are settings now (enable_exr_loading / enable_oiio,
REM on by default); SLATE_ENABLE_EXR_LOADING / SLATE_ENABLE_OIIO still override.

"%PORTABLE_PYTHON%" slate/gatekeeper_main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] Application exited with error code %ERRORLEVEL%
    pause
)
