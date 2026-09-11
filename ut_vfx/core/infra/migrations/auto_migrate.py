import os
import logging
import subprocess

logger = logging.getLogger(__name__)

def run_auto_migrations(active_mode: str = None, fallback_used: bool = None) -> bool:
    """
    Safely executes Alembic migrations.
    Finds the root alembic.ini and runs `alembic upgrade head`.
    This is safe to call repeatedly as Alembic will just do nothing if up to date.

    `active_mode` and `fallback_used` describe the backend that is already
    running. DatabaseManager passes its own, because it calls this from inside
    its constructor: asking the global `database_manager` there would ask for a
    manager that does not exist yet, which built a second one, which ran the
    migrations again, and so on - a hundred and sixty times per start-up, and
    sometimes all the way to a RecursionError.
    """
    try:
        from ..global_config import GlobalConfig
        if GlobalConfig.get_db_mode() != "postgres":
            logger.info("Skipping Alembic auto-migrations (not in Postgres mode).")
            return True

        if active_mode is None or fallback_used is None:
            # No backend handed to us: safe here, because nothing is mid-build.
            from ..database_manager import database_manager
            active_mode = str(getattr(database_manager, "active_mode", ""))
            fallback_used = bool(getattr(database_manager, "fallback_used", False))

        if str(active_mode).lower() != "postgres" or fallback_used:
            logger.info("Skipping Alembic auto-migrations (not active Postgres backend).")
            return True

        # Resolve root dir (D:\Soft\UTCAP\V0040)
        # We are in ut_vfx/core/infra/migrations/auto_migrate.py
        current_dir = os.path.dirname(os.path.abspath(__file__))
        infra_dir = os.path.dirname(current_dir)
        core_dir = os.path.dirname(infra_dir)
        ut_vfx_dir = os.path.dirname(core_dir)
        root_dir = os.path.dirname(ut_vfx_dir)
        
        alembic_ini_path = os.path.join(root_dir, "alembic.ini")
        if not os.path.exists(alembic_ini_path):
            logger.warning(f"Auto-migration skipped: No alembic.ini found at {alembic_ini_path}")
            return False

        # Use portable python alembic if it exists
        portable_root = os.path.dirname(root_dir)
        alembic_exe = os.path.join(portable_root, "python_portable", "Scripts", "alembic.exe")
        
        if not os.path.exists(alembic_exe):
            alembic_exe = "alembic" # Fallback to system path
            
        logger.info("Executing database migrations (alembic upgrade head)...")
        result = subprocess.run(
            [alembic_exe, "upgrade", "head"], 
            cwd=root_dir, 
            check=False, 
            capture_output=True, 
            text=True
        )
        
        if result.returncode != 0:
            logger.error(f"Alembic migration failed (Exit {result.returncode}):\n{result.stderr}")
            return False
            
        logger.info("Database schema is up to date.")
        return True
    except Exception as e:
        logger.error(f"Failed to execute auto-migrations: {e}")
        return False
