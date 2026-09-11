@echo off
REM ============================================================================
REM Apply Database Indexes - Python Version (No psql Required)
REM Uses your existing PostgreSQL connection via psycopg2
REM ============================================================================

echo.
echo Applying database indexes using Python...
echo.

python database\apply_indexes.py

if errorlevel 1 (
    echo.
    echo [ERROR] Index application failed!
    pause
    exit /b 1
)

echo.
pause
