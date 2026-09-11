
@echo off
"%LOCALAPPDATA%\Programs\Python\Python310\python.exe" ".\slate\scripts\fix_schema_overflow.py"
exit /b %ERRORLEVEL%
