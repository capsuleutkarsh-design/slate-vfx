
@echo off
"%LOCALAPPDATA%\Programs\Python\Python310\python.exe" ".\ut_vfx\scripts\verify_schema.py" > ".\verify_result.txt" 2>&1
echo Done
