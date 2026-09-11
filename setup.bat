@echo off
REM ===========================================================================
REM  Slate - first run setup
REM
REM  Double-click this. It downloads everything Slate needs that is not source
REM  code, puts it where Slate looks for it, and asks for the studio's database
REM  details once.
REM
REM  Nothing is installed system-wide. No Python already on this machine is
REM  touched. Running it twice is safe - anything already in place is skipped.
REM
REM    setup.bat              install what is missing
REM    setup.bat /check       say what it would do, download nothing
REM    setup.bat /force       re-download even what is already there
REM    setup.bat /server      also install PostgreSQL (only for the machine
REM                           that runs Slate Server)
REM ===========================================================================
setlocal enabledelayedexpansion

set "SLATE_ROOT=%~dp0"
set "ARGS="

REM Translate the slash switches people expect from a .bat into the dash
REM switches PowerShell understands, so both spellings work.
:parse
if "%~1"=="" goto run
set "SW=%~1"
if /i "!SW!"=="/check"      set "ARGS=!ARGS! -Check"      & shift & goto parse
if /i "!SW!"=="/force"      set "ARGS=!ARGS! -Force"      & shift & goto parse
if /i "!SW!"=="/server"     set "ARGS=!ARGS! -Server"     & shift & goto parse
if /i "!SW!"=="/skipconfig" set "ARGS=!ARGS! -SkipConfig" & shift & goto parse
set "ARGS=!ARGS! !SW!"
shift
goto parse

:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SLATE_ROOT%setup\install.ps1" -Root "%SLATE_ROOT%." !ARGS!
set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
    echo.
    echo Setup did not finish. The messages above say what stopped it.
    echo Nothing was half-installed - run setup.bat again once it is sorted.
)

echo.
pause
exit /b %RESULT%
