import sys
import datetime
from decimal import Decimal

# Ensure the module can be loaded
sys.path.append('d:/Soft/Slate/V0040')

from slate.core.infra.database_manager import database_manager

def populate():
    # 1. Insert Dummy Users
    dummy_users = [
        {"username": "DUM001", "password_hash": "dummy", "role": "Artist", "display_name": "Alice Artist", "job_title": "3D Generalist", "department": "3D"},
        {"username": "DUM002", "password_hash": "dummy", "role": "Supervisor", "display_name": "Bob Supervisor", "job_title": "VFX Supervisor", "department": "Production"},
        {"username": "DUM003", "password_hash": "dummy", "role": "HR", "display_name": "Carol HR", "job_title": "HR Manager", "department": "HR"},
        {"username": "DUM004", "password_hash": "dummy", "role": "Manager", "display_name": "Dave Manager", "job_title": "Production Manager", "department": "Production"},
        {"username": "DUM005", "password_hash": "dummy", "role": "Artist", "display_name": "Eve Compositor", "job_title": "Nuke Compositor", "department": "Compositing"}
    ]

    for u in dummy_users:
        # Check if user exists
        exists = database_manager.execute_query(f"SELECT * FROM ut_users WHERE username = '{u['username']}'")
        if not exists:
            # We don't have all exact columns of ut_users, but let's try a standard insert for common ones
            query = f"""
                INSERT INTO ut_users (username, password_hash, roles, display_name, job_title) 
                VALUES ('{u['username']}', '{u['password_hash']}', '{u['role']}', '{u['display_name']}', '{u['job_title']}')
            """
            try:
                database_manager.execute_update(query)
                print(f"Inserted user {u['username']}")
            except Exception as e:
                print(f"Failed to insert user {u['username']}: {e}")

    # 2. Insert Leave Requests
    leaves = [
        {"user_id": "DUM001", "type": "Sick Leave", "start_date": "2026-07-01", "end_date": "2026-07-02", "status": "Approved", "approved_by": "DUM002"},
        {"user_id": "DUM001", "type": "Casual Leave", "start_date": "2026-07-20", "end_date": "2026-07-22", "status": "Pending", "approved_by": ""},
        {"user_id": "DUM002", "type": "Earned Leave", "start_date": "2026-08-01", "end_date": "2026-08-10", "status": "Approved", "approved_by": "DUM003"},
        {"user_id": "DUM005", "type": "Sick Leave", "start_date": "2026-07-15", "end_date": "2026-07-15", "status": "Pending", "approved_by": ""}
    ]
    
    # clear leaves just in case to prevent duplicates on rerun
    database_manager.execute_update("DELETE FROM leave_requests")
    for l in leaves:
        query = f"""
            INSERT INTO leave_requests (user_id, type, start_date, end_date, status, approved_by)
            VALUES ('{l['user_id']}', '{l['type']}', '{l['start_date']}', '{l['end_date']}', '{l['status']}', '{l['approved_by']}')
        """
        database_manager.execute_update(query)

    # 3. Insert Payroll Records
    payrolls = [
        {"user_id": "DUM001", "month": "Jun 2026", "base_salary": 45000, "overtime_hours": 12, "total": 48000, "status": "Paid"},
        {"user_id": "DUM001", "month": "May 2026", "base_salary": 45000, "overtime_hours": 0, "total": 45000, "status": "Paid"},
        {"user_id": "DUM002", "month": "Jun 2026", "base_salary": 90000, "overtime_hours": 0, "total": 90000, "status": "Paid"},
        {"user_id": "DUM003", "month": "Jun 2026", "base_salary": 65000, "overtime_hours": 5, "total": 66500, "status": "Paid"},
        {"user_id": "DUM005", "month": "Jun 2026", "base_salary": 50000, "overtime_hours": 20, "total": 55000, "status": "Paid"}
    ]
    
    database_manager.execute_update("DELETE FROM payroll_records")
    for p in payrolls:
        query = f"""
            INSERT INTO payroll_records (user_id, month, base_salary, overtime_hours, total, status)
            VALUES ('{p['user_id']}', '{p['month']}', {p['base_salary']}, {p['overtime_hours']}, {p['total']}, '{p['status']}')
        """
        database_manager.execute_update(query)

    # 4. Insert Onboarding Workflows
    onboarding = [
        {"user_id": "DUM001", "task_name": "IT Setup", "department": "IT", "is_completed": True},
        {"user_id": "DUM001", "task_name": "HR Orientation", "department": "HR", "is_completed": True},
        {"user_id": "DUM005", "task_name": "IT Setup", "department": "IT", "is_completed": True},
        {"user_id": "DUM005", "task_name": "Project Assignment", "department": "Production", "is_completed": False}
    ]
    
    database_manager.execute_update("DELETE FROM onboarding_workflows")
    for o in onboarding:
        query = f"""
            INSERT INTO onboarding_workflows (user_id, task_name, department, is_completed)
            VALUES ('{o['user_id']}', '{o['task_name']}', '{o['department']}', {'TRUE' if o['is_completed'] else 'FALSE'})
        """
        database_manager.execute_update(query)

    print("Dummy data populated successfully.")

if __name__ == '__main__':
    populate()
