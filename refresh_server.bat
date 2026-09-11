@echo off
echo ==============================================
echo UT_VFX Database Server Refresher
echo ==============================================
echo.
echo Stopping UT_Server.exe processes...
taskkill /f /im "UT_Server.exe" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Server process terminated.
) else (
    echo No UT_Server process found running.
)

echo.
echo Stopping launch_server.bat processes...
taskkill /f /fi "WINDOWTITLE eq UT_Server" >nul 2>&1

echo.
echo Wait 2 seconds before restarting...
timeout /t 2 /nobreak >nul

echo.
echo Restarting Server...
start "UT_Server" cmd /c "launch_server.bat"

echo.
echo Server refresh complete! You can close this window.
timeout /t 5 >nul
