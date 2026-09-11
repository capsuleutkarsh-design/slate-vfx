@echo off
echo ======================================
echo  Running Test Suite with Coverage
echo ======================================
echo.

cd /d "%~dp0.."

echo Installing pytest-cov (if needed)...
pip install -q pytest-cov
echo.

echo Running tests with coverage analysis...
python -m pytest --cov=ut_vfx --cov-report=term-missing --cov-report=html tests/
echo.

if %ERRORLEVEL% EQU 0 (
    echo ======================================
    echo  Tests Completed Successfully!
    echo ======================================
    echo.
    echo Coverage report generated:
    echo   - Terminal: Above output
    echo   - HTML: htmlcov\index.html
    echo.
    echo Opening HTML coverage report...
    start htmlcov\index.html
) else (
    echo ======================================
    echo  Some Tests Failed
    echo ======================================
    echo Please review the output above.
)

pause
