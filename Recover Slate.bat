@echo off
REM Recover Slate - for when nobody can sign in. Run on the server PC.
REM   Recover Slate.bat            the window
REM   Recover Slate.bat health     what is wrong, in plain words
REM   Recover Slate.bat --help     everything else
REM If the Recovery Key is lost: right-click, "Run as administrator", and make a new one.
cd /d "%~dp0"
set "PORTABLE_PYTHON=%~dp0runtime\python\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\Scripts\python.exe"
if not exist "%PORTABLE_PYTHON%" set "PORTABLE_PYTHON=%~dp0..\python_portable\python.exe"

if exist "%PORTABLE_PYTHON%" (
    set "PYTHONPATH=%~dp0"
    "%PORTABLE_PYTHON%" "%~dp0slate_recover.py" %*
    goto :done
)
REM An installed server: the same tool is inside Slate_Server.exe.
if exist "%~dp0Slate_Server.exe" (
    "%~dp0Slate_Server.exe" --recover %*
    goto :done
)
if exist "%LOCALAPPDATA%\Programs\Slate Server\Slate_Server.exe" (
    "%LOCALAPPDATA%\Programs\Slate Server\Slate_Server.exe" --recover %*
    goto :done
)
echo [ERROR] Neither Python nor Slate_Server.exe was found next to this file.
:done
if not "%~1"=="" pause
