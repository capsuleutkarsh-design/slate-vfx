@echo off
set "REZ_DIR=%LOCALAPPDATA%\Programs\Python\Python310\Scripts"

echo Using Rez Python environment: %REZ_DIR%
echo.
echo Installing fileseq into base Python...
call "%REZ_DIR%\pip.exe" install fileseq

echo.
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] fileseq installed successfully!
    echo.
    echo NOTE: Since 'rez-pip' was missing, we installed directly to Python.
    echo This should work if your rez 'python' package uses this interpreter.
) else (
    echo [ERROR] Failed to install fileseq.
)
pause
