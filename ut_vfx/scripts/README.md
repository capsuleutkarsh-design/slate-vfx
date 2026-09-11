# Utility Scripts Directory

This directory contains development and maintenance scripts for UT_VFX.

## Scripts Overview

### Help & Documentation Generators

- `complete_help_update.py` - Comprehensive help content updater
- `generate_full_help.py` - Full help documentation generator
- `create_json_help.py` - JSON help content creator
- `update_tester_help.py` - Tester panel help updater
- `final_tester_docs.py` - Final tester documentation generator

### Database & Data Management

- `debug_thumb_db.py` - Thumbnail database debugging tool
- `attendance_cleanup.py` - Attendance data cleanup utility

### Development Tools

- `add_remaining_tabs.py` - Add missing tabs to the application
- `convert_emojis.py` - Emoji conversion utility
- `fix_quotes.py` - Quote character fixer

## Usage

These scripts are for development and maintenance purposes only. They should NOT be included in production builds.

Run scripts from the project root:

```bash
python -m ut_vfx.scripts.<script_name>
```

## Note

These scripts may have dependencies on the main application modules. Ensure the development environment is properly set up before running.
