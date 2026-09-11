
import sys
import logging

# Ensure we can import core modules
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ut_vfx.core.infra.postgres_manager import PostgresManager

# Setup basic logging
logging.basicConfig(level=logging.INFO)

def fix_schema():
    logging.info("Initializing PostgresManager...")
    db = PostgresManager()
    
    logging.info("Checking 'stock_library' schema...")
    
    # Check current column types
    check_sql = """
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'stock_library' AND column_name IN ('file_size', 'id');
    """
    
    rows = db.execute_query(check_sql, fetch="all")
    logging.info("Current Schema:")
    for row in rows:
        logging.info(f" - {row['column_name']}: {row['data_type']}")
        
    logging.info("\nApplying Schema Fixes (INTEGER -> BIGINT)...")
    
    try:
        # Alter file_size
        db.execute_update("ALTER TABLE stock_library ALTER COLUMN file_size TYPE BIGINT;")
        logging.info(" -> 'file_size' updated to BIGINT")
        
        # Alter id (if necessary, usually id is serial/integer, strictly speaking we might want BIGSERIAL but BIGINT is fine for type)
        db.execute_update("ALTER TABLE stock_library ALTER COLUMN id TYPE BIGINT;")
        logging.info(" -> 'id' updated to BIGINT")
        
        logging.info("\n✅ Schema Fix Applied Successfully!")
        
    except Exception as e:
        logging.exception(f"\n❌ FAILED to update schema: {e}")

if __name__ == "__main__":
    fix_schema()
