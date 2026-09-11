
@echo off
"%LOCALAPPDATA%\Programs\Python\Python310\python.exe" ".\slate\scripts\test_user_load.py" > ".\user_test_result.txt" 2>&1
echo Done
