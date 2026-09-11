import sys

# Ensure the module can be loaded
sys.path.append('d:/Soft/UTCAP/V0040')

from ut_vfx.core.infra.database_manager import database_manager

def populate():
    # 1. Insert Hardware
    hardware = [
        {"machine_name": "WS-3D-001", "assigned_to": "DUM001", "gpu": "RTX 4090", "ram": "128GB", "status": "Active"},
        {"machine_name": "WS-CMP-002", "assigned_to": "DUM005", "gpu": "RTX A6000", "ram": "256GB", "status": "Active"},
        {"machine_name": "WS-PRD-003", "assigned_to": "DUM004", "gpu": "RTX 3080", "ram": "64GB", "status": "Active"},
        {"machine_name": "WS-RDR-001", "assigned_to": "", "gpu": "Dual RTX 4090", "ram": "512GB", "status": "Repair"}
    ]
    
    database_manager.execute_update("DELETE FROM hardware_inventory")
    for h in hardware:
        query = f"""
            INSERT INTO hardware_inventory (machine_name, assigned_to, gpu, ram, status)
            VALUES ('{h['machine_name']}', '{h['assigned_to']}', '{h['gpu']}', '{h['ram']}', '{h['status']}')
        """
        database_manager.execute_update(query)

    # 2. Insert IT Tickets
    tickets = [
        {"submitted_by": "DUM005", "category": "Hardware", "description": "Monitor 2 flickering", "priority": "Medium", "status": "Open", "resolved_at": "NULL"},
        {"submitted_by": "DUM001", "category": "Software", "description": "Maya license server unreachable", "priority": "High", "status": "In Progress", "resolved_at": "NULL"},
        {"submitted_by": "DUM003", "category": "Network", "description": "Cannot connect to central NAS", "priority": "Critical", "status": "Resolved", "resolved_at": "'2026-07-10'"}
    ]
    
    database_manager.execute_update("DELETE FROM it_tickets")
    for t in tickets:
        query = f"""
            INSERT INTO it_tickets (submitted_by, category, description, priority, status, resolved_at)
            VALUES ('{t['submitted_by']}', '{t['category']}', '{t['description']}', '{t['priority']}', '{t['status']}', {t['resolved_at']})
        """
        database_manager.execute_update(query)

    print("IT data populated successfully.")

if __name__ == '__main__':
    populate()
