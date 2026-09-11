@echo off
REM ============================================================================
REM Project Root Cleanup - Organize Files into Proper Directories
REM Created: 2026-01-30
REM ============================================================================

echo.
echo ╔═══════════════════════════════════════════════════════════════════════╗
echo ║              Project Root Directory Cleanup                           ║
echo ╚═══════════════════════════════════════════════════════════════════════╝
echo.

echo [1/7] Moving debug files to debug_archive...
if not exist debug_archive mkdir debug_archive
move /Y debug_crash.py debug_archive\ 2>nul
move /Y debug_gfx.py debug_archive\ 2>nul
move /Y debug_new_ocio.py debug_archive\ 2>nul
move /Y debug_otio.py debug_archive\ 2>nul
move /Y rez_pip_debug.log debug_archive\ 2>nul
echo   ✓ Debug files moved

echo.
echo [2/7] Moving migration helper files to migrations...
move /Y migration_notes.md migrations\ 2>nul
echo   ✓ Migration files moved

echo.
echo [3/7] Removing obsolete Kitsu files...
if exist kitsu-backend rmdir /s /q kitsu-backend
del /f /q start_kitsu.bat 2>nul
del /f /q stop_kitsu.bat 2>nul
echo   ✓ Kitsu files removed

echo.
echo [4/7] Moving documentation to docs...
move /Y CHANGELOG.md docs\ 2>nul
move /Y BUILD_INSTRUCTIONS.md docs\ 2>nul
move /Y DIRECTORY_STRUCTURE.md docs\ 2>nul
echo   ✓ Documentation organized

echo.
echo [5/7] Moving build files to tools...
move /Y build_pipeline.py tools\ 2>nul
echo   ✓ Build files moved

echo.
echo [6/7] Moving utility scripts to scripts...
move /Y cleanup_files.bat scripts\ 2>nul
echo   ✓ Utility scripts organized

echo.
echo [7/7] Cleaning up test artifacts...
if exist .coverage del /f /q .coverage
if exist .pytest_cache rmdir /s /q .pytest_cache
if exist htmlcov rmdir /s /q htmlcov
if exist error_log.txt del /f /q error_log.txt
echo   ✓ Test artifacts cleaned

echo.
echo ╔═══════════════════════════════════════════════════════════════════════╗
echo ║                    CLEANUP COMPLETE!                                  ║
echo ╚═══════════════════════════════════════════════════════════════════════╝
echo.
echo Root directory is now organized!
echo.
echo Current structure:
echo   - Core files: client_config.json, pyproject.toml
echo   - Launch scripts: launch_app.bat
echo   - Organized folders: ut_vfx/, docs/, database/, scripts/, tools/
echo.

pause
