@echo off
echo Running UTVFX Unit Tests...
echo.
python -m unittest discover -s tests -p "test_*.py"
echo.
pause
