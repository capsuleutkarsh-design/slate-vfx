from ut_vfx.core.infra.postgres_manager import PostgresManager
import sys

try:
    db = PostgresManager()
    
    print("\nVERIFICATION RESULTS:")
    
    # Projects
    res = db.execute_query("SELECT COUNT(*) as c FROM tracking_projects", fetch="one")
    print(f"Projects: {res['c']}")
    
    # Shots
    res = db.execute_query("SELECT COUNT(*) as c FROM tracking_shots", fetch="one")
    print(f"Shots: {res['c']}")
    
    # Tasks
    res = db.execute_query("SELECT COUNT(*) as c FROM tracking_tasks", fetch="one")
    print(f"Tasks: {res['c']}")
    
except Exception as e:
    print(f"Verification Failed: {e}")
