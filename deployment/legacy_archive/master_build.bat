@echo off
color 0B
title UT_VFX Release Pipeline

REM Ensure we run from Project Root (Parent of this script)
cd /d "%~dp0\.."

echo ========================================================
echo   UT_VFX - MASTER RELEASE PIPELINE
echo ========================================================
echo.
echo Phase 1: VERSION MANAGEMENT
echo ------------------------------------------

REM Run the python bumper interactively
REM It will ask for the version number and update __init__.py and .iss
python tools/bump_version.py
if errorlevel 1 goto failed

echo.
echo Phase 2: BUILDING EXECUTABLES
echo ------------------------------------------
echo Calling PyInstaller builder...
call deployment\pyinstaller_onedir_with_deps_and_icon_absolute_fixed_v2.bat
if errorlevel 1 goto failed

echo.
echo Phase 3: PACKAGING INSTALLER
echo ------------------------------------------
echo Compiling Inno Setup script...

REM Check if ISCC is in PATH, otherwise look in default locations
REM Find Inno Setup Compiler
set "ISCC_PATH="

REM 1. Check Path
where iscc >nul 2>nul
if %errorlevel% equ 0 set "ISCC_PATH=iscc"

REM 2. Check Standard Install Location (Override if found)
if exist "C:\Program Files\Inno Setup 7\ISCC.exe" set "ISCC_PATH=C:\Program Files\Inno Setup 7\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if not defined ISCC_PATH (
    echo WARNING: Inno Setup Compiler (ISCC.exe) not found!
    echo Please install Inno Setup 7 or add it to PATH.
    goto failed
)

:run_iscc
echo Found ISCC...
"%ISCC_PATH%" deployment\setup_ut_vfx.iss

if errorlevel 1 goto failed

color 0A
echo.
echo ========================================================
echo    RELEASE COMPLETE!
echo ========================================================
echo.
echo Installer located in: Installers/
echo.
pause
exit

:failed
color 0C
echo.
echo ========================================================
echo    PIPELINE FAILED. CHECK LOGS ABOVE.
echo ========================================================
pause
