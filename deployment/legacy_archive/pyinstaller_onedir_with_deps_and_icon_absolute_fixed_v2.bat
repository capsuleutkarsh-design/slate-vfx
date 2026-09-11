@echo off
color 0B
echo ========================================================
echo      UT_VFX - DUAL BUILDER (v3.0)
echo ========================================================
echo.

REM 1. Clean previous builds
echo [1/4] Cleaning artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
echo       Clean complete.
echo.

REM 2. Build Main App (The Core Software)
echo [2/4] Compiling MAIN APP (UTVFX)...
echo       This takes a minute...
pyinstaller --clean --noconfirm deployment\UTVFX.spec
if errorlevel 1 goto failed

REM 3. Build Launcher (The Updater)
echo.
echo [3/4] Compiling LAUNCHER (Auto-Updater)...
pyinstaller --clean --noconfirm deployment\Launcher.spec
if errorlevel 1 goto failed

REM 4. Organization (Optional but Recommended)
REM Move the Launcher EXE next to the Main EXE for easy testing,
REM or keep them separate folders. By default, PyInstaller makes two folders in dist.

if exist "dist\UTVFX\UTVFX.exe" (
    if exist "dist\CapsuleLauncher\CapsuleLauncher.exe" (
        goto success
    )
)

:failed
color 0C
echo.
echo ========================================================
echo    CRITICAL ERROR: BUILD FAILED.
echo ========================================================
exit /b 1

:success
color 0A
echo.
echo ========================================================
echo    SUCCESS! BOTH APPS BUILT.
echo ========================================================
echo.
echo    1. MAIN APP: dist\UTVFX\UTVFX.exe
echo       (Put this entire folder on X:\Extra\UT_Central\Updates)
echo.
echo    2. LAUNCHER: dist\CapsuleLauncher\CapsuleLauncher.exe
echo       (Distribute THIS file to Artist Desktops)
echo.
exit /b 0