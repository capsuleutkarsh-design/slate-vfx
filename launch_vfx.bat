@echo off
REM Run from this folder, whatever "Start in" a shortcut gives.
cd /d "%~dp0"
REM One Python for every launcher, looked for in this order:
REM   1. runtime\python        - what setup.bat installs, inside this checkout
REM   2. ..\python_portable    - the shared environment beside the checkout
set "PORTABLE_PYTHON=%~dp0runtime\python\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\python.exe"

if not exist "%PORTABLE_PYTHON%" (
    echo [ERROR] Python was not found. Slate looked in:
    echo   %~dp0runtime\python\python.exe
    echo   %~dp0..\python_portable\Scripts\python.exe
    echo   %~dp0..\python_portable\python.exe
    echo Run setup.bat first.
    pause
    exit /b 1
)

echo [INFO] Using Portable Python Environment...
echo [INFO] Starting Slate VFX...

REM EXR and OpenImageIO are settings now (enable_exr_loading / enable_oiio,
REM on by default); SLATE_ENABLE_EXR_LOADING / SLATE_ENABLE_OIIO still override.

"%PORTABLE_PYTHON%" slate/vfx_studio_main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] Slate VFX exited with error code %ERRORLEVEL%
    pause
)
