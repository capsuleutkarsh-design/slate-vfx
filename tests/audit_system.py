import sys
import os
import logging

# Add project root to path
sys.path.append(os.getcwd())

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def run_audit():
    print("=========================================")
    print("      UT_VFX SYSTEM AUDIT           ")
    print("=========================================")
    
    errors = []
    warnings = []
    
    # 1. Dependency Check
    print("\n[1/5] Checking Core Dependencies...")
    try:
        import psycopg2
        import numpy
        print("  [OK] psycopg2 (PostgreSQL Driver)")
        print("  [OK] numpy (Vector Math)")
    except ImportError as e:
        print(f"  [FAIL] Missing Dependency: {e}")
        errors.append(f"Missing Dependency: {e}")

    # 2. Database Connectivity
    print("\n[2/5] Checking Database Connectivity...")
    db = None
    try:
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        db = PostgresManager()
        # Simple query to verify connection
        res = db.execute_query("SELECT version()", fetch="one")
        if res:
            print(f"  [OK] Connected: {res['version'][:50]}...")
        else:
            print("  [FAIL] Connected but returned no version.")
            errors.append("Database connection test failed.")
    except Exception as e:
        print(f"  [FAIL] Connection Error: {e}")
        errors.append(f"Database Connection Error: {e}")
        return # Critical failure

    # 3. Data Integrity & Migration Verification
    print("\n[3/5] Verifying Migrated Data...")
    tables_to_check = {
        "stock_library": 10000, # Expecting 11k+
        "users": 1,             # Expecting some users
        "projects": 1,          # Expecting some projects
        "tracking_shots": 100   # Expecting shots
    }
    
    for table, min_count in tables_to_check.items():
        try:
            count_res = db.execute_query(f"SELECT COUNT(*) as c FROM {table}", fetch="one")
            count = count_res['c']
            if count >= min_count:
                print(f"  [OK] Table '{table}': Found {count} rows (Expected > {min_count})")
            else:
                print(f"  [WARN] Table '{table}': Found {count} rows (Low count?)")
                warnings.append(f"Table '{table}' has low row count: {count}")
        except Exception as e:
            print(f"  [FAIL] Could not query table '{table}': {e}")
            errors.append(f"Query failed checking table '{table}'")

    # 4. Functional Test: Vector Search
    print("\n[4/5] Testing Vector Search Implementation...")
    try:
        # Create a dummy vector of 512 zeros (or whatever the dimension is)
        # We need to know the dimension. Usually 512 or 768. Let's try to infer from DB if possible
        # or just try a standard size.
        
        # Check one valid embedding
        sample = db.execute_query("SELECT embedding FROM stock_library WHERE embedding IS NOT NULL LIMIT 1", fetch="one")
        
        if sample and sample['embedding']:
            import json
            # loading correct format
            emb_data = sample['embedding']
            if isinstance(emb_data, str):
                emb_data = json.loads(emb_data)
                
            dim = len(emb_data)
            print(f"  [INFO] Detected Vector Dimension: {dim}")
            
            # Test Search
            dummy_query = [0.1] * dim
            results = db.search_similar_assets(dummy_query, limit=5)
            
            if isinstance(results, list):
                print(f"  [OK] Search executed successfully. Returned {len(results)} results.")
            else:
                print(f"  [FAIL] Search returned invalid type: {type(results)}")
                errors.append("Vector search execution failed.")
        else:
            print("  [WARN] No embeddings found in DB to test dimension. Skipping operational test.")
            warnings.append("No embeddings available to test vector search.")
            
    except Exception as e:
        print(f"  [FAIL] Vector Search Logic Crashed: {e}")
        errors.append(f"Vector search crashed: {e}")

    # 5. File System Access
    print("\n[5/5] Checking Server Mount Access...")
    server_root = r"X:\Extra\UT_Central"
    if os.path.exists(server_root):
        print(f"  [OK] Server Root Found: {server_root}")
    else:
        print(f"  [WARN] Server Root NOT Found: {server_root}")
        print("         (This is expected if X: drive is not mapped on this specific machine)")
        warnings.append("X: Drive not accessible (Check network map)")

    # Summary
    print("\n=========================================")
    print("            AUDIT RESULTS                ")
    print("=========================================")
    if not errors and not warnings:
        print("RESULT: PASSED (100% Clean)")
    elif errors:
        print(f"RESULT: FAILED ({len(errors)} Errors, {len(warnings)} Warnings)")
    else:
        print(f"RESULT: PASSED WITH WARNINGS ({len(warnings)} Warnings)")
        
    for w in warnings:
        print(f"  [WARN] {w}")
    for e in errors:
        print(f"  [ERR] {e}")

if __name__ == "__main__":
    run_audit()
