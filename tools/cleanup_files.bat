
@echo off
if not exist debug_archive mkdir debug_archive
move /Y debug_*.py debug_archive\
move /Y verify_*.py debug_archive\
move /Y add_methods.py debug_archive\
move /Y temp_methods.py debug_archive\
move /Y demo_pyblish.py debug_archive\
move /Y test_progress.py debug_archive\
move /Y test_telemetry.py debug_archive\
rmdir /S /Q temp_debug_project
rmdir /S /Q temp_deep_debug
rmdir /S /Q temp_seq_test
echo Cleanup Done
