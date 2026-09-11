# Tools Directory Organization

## Archive (tools/archive/)

**Historical scripts - migration complete, kept for reference:**

- `migrate_legacy_db.py` - Legacy SQLite to PostgreSQL migration
- `migrate_sqlite_to_postgres.py` - Database migration script
- `migrate_stock_data.py` - Stock library migration
- `reset_db_password.py` - Old password reset (has hardcoded password)
- `upgrade_schema_dashboard.py` - Schema upgrade script (has hardcoded password)
- `setup_postgres_db.py` - Initial database setup
- `codebase_analysis.md` - Pre-Phase 1 analysis
- `codebase_analysis_v2.md` - Second analysis

**Status:** ⚠️ These contain hardcoded passwords - use for reference only, not production!

---

## Utilities (tools/utilities/)

**Diagnostic and maintenance scripts:**

- `check_db_location.py` - Verify database paths
- `debug_db_paths.py` - Debug path resolution
- `debug_projects_data.py` - Inspect project data
- `debug_stock_db.py` - Stock library diagnostics
- `inspect_legacy_data.py` - Legacy data inspection
- `inspect_sqlite.py` - SQLite database inspection
- `create_client_config.py` - Generate client config files

**Usage:** Run when troubleshooting issues

---

## Active Tools (tools/)

**Production build and maintenance:**

- `build_pipeline.py` - **Main build script** ⭐
- `bump_version.py` - Version management
- Other active development tools...

---

## Notes

- Archive folder contains historical scripts post-migration
- Utilities are for diagnostics, not regular use
- Main build process: `python tools/build_pipeline.py`
