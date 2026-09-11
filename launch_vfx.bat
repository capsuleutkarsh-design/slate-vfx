@echo off
set "PORTABLE_PYTHON=%~dp0..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\python.exe"

if not exist "%PORTABLE_PYTHON%" (
    echo [ERROR] Portable Python not found at %PORTABLE_PYTHON%
    pause
    exit /b 1
)

echo [INFO] Using Portable Python Environment...
echo [INFO] Launching UT VFX Studio...

set "UTVFX_ENABLE_EXR_LOADING=1"
set "UTVFX_ENABLE_OIIO=1"
echo [INFO] EXR loading enabled (UTVFX_ENABLE_EXR_LOADING=1)
echo [INFO] OIIO path enabled (UTVFX_ENABLE_OIIO=1)

"%PORTABLE_PYTHON%" ut_vfx/vfx_studio_main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] UT VFX Studio exited with error code %ERRORLEVEL%
    pause
)
