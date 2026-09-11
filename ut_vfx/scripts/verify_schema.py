import logging

import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ut_vfx.core.infra.postgres_manager import PostgresManager

def verify():
    logging.info("Verifying schema...")
    try:
        # Initialize config to ensure DB connection params are loaded if needed
        # PostgresManager usually loads from env or config file
        db = PostgresManager()
        
        # Check if table exists and column types
        with db.get_cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'stock_library' 
                AND column_name IN ('id', 'file_size');
            """)
            rows = cur.fetchall()
            if not rows:
                logging.info("Table 'stock_library' not found or columns missing.")
                return

            logging.info("Current Schema:")
            for row in rows:
                logging.info(f"  {row[0]}: {row[1]}")
                
    except Exception as e:
        logging.exception(f"Verification failed: {e}")

if __name__ == "__main__":
    try:
        verify()
    except Exception as e:
        logging.exception(f"Script failed: {e}")
