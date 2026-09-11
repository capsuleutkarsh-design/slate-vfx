@echo off
REM ============================================================================
REM Database Index Creation Script
REM Run this to add performance indexes to PostgreSQL database
REM ============================================================================

echo.
echo ╔═══════════════════════════════════════════════════════════════════════╗
echo ║         PostgreSQL Index Creation for UT_VFX                    ║
echo ╚═══════════════════════════════════════════════════════════════════════╝
echo.

REM Check if psql is available
where psql >nul 2>&1
if errorlevel 1 (
    echo [WARNING] psql command not found!
    echo.
    echo Please install PostgreSQL client tools or run the SQL manually:
    echo   1. Copy database\create_indexes.sql
    echo   2. Run it using pgAdmin or your preferred SQL client
    echo.
    pause
    exit /b 1
)

echo Connecting to PostgreSQL server at 172.16.1.45...
echo.

REM Prompt for password
set /p DB_PASSWORD="Enter PostgreSQL password for user 'postgres': "

echo.
echo Creating performance indexes...
echo.

psql -h 172.16.1.45 -U postgres -d ut_vfx -f database\create_indexes.sql

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create indexes!
    echo Check connection to 172.16.1.45 and verify credentials.
    pause
    exit /b 1
)

echo.
echo ╔═══════════════════════════════════════════════════════════════════════╗
echo ║                   INDEXES CREATED SUCCESSFULLY!                       ║
echo ╚═══════════════════════════════════════════════════════════════════════╝
echo.
echo Performance improvements:
echo   ✓ Stock library searches - Faster
echo   ✓ Project/shot queries - Faster
echo   ✓ Tag searches - Full-text indexed
echo.
echo Next: Restart UT_VFX to see improved performance.
echo.

pause
