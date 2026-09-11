import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

try:
    from ut_vfx.core.infra.postgres_manager import PostgresManager
except ImportError:
    print("Could not import PostgresManager.")
    sys.exit(1)

def show_data_dir():
    try:
        db = PostgresManager()
        # "SHOW data_directory" is a Postgres command that tells you the physical folder
        res = db.execute_query("SHOW data_directory", fetch="one")
        print(f"\n--- Physical Database Location ---")
        print(f"Path: {res['data_directory']}")
        print(f"----------------------------------\n")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    show_data_dir()
