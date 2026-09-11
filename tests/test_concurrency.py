"""
Concurrency Stress Test for JSON Operations.

This test uses the 'multiprocessing' module to simulate multiple workers
writing to the same JSON file simultaneously. It ensures that 'SafeJsonIO'
correctly handles file locks and atomic updates without data corruption.
"""

import multiprocessing
import time
import os
from pathlib import Path
from ut_vfx.utils.safe_json import SafeJsonIO

TEST_FILE = Path("concurrency_test.json")

def worker(worker_id):
    """Worker process that increments a counter in the JSON file."""
    for i in range(5):
        try:
            # Random sleep to induce race conditions
            time.sleep(0.1 * (worker_id + 1))
            
            # Atomic Update
            def increment(d):
                count = d.get("count", 0)
                count += 1
                d["count"] = count
                d[f"worker_{worker_id}"] = d.get(f"worker_{worker_id}", 0) + 1
            
            success = SafeJsonIO.update_json(TEST_FILE, increment)
            if success:
                print(f"Worker {worker_id}: Incremented")
            else:
                print(f"Worker {worker_id}: FAILED to increment")
            
        except Exception as e:
            print(f"Worker {worker_id} Error: {e}")

def run_test():
    # Setup
    if TEST_FILE.exists(): os.remove(TEST_FILE)
    
    # Initialize file
    SafeJsonIO.save_json(TEST_FILE, {"count": 0})
    
    processes = []
    num_workers = 4
    
    print(f"Starting {num_workers} workers...")
    
    for i in range(num_workers):
        p = multiprocessing.Process(target=worker, args=(i,))
        processes.append(p)
        p.start()
        
    for p in processes:
        p.join()
        
    # Verify
    final_data = SafeJsonIO.load_json(TEST_FILE)
    expected_count = num_workers * 5
    actual_count = final_data.get("count", 0)
    
    print("-" * 30)
    print(f"Expected Count: {expected_count}")
    print(f"Actual Count:   {actual_count}")
    
    if expected_count == actual_count:
        print("SUCCESS: No race conditions detected.")
    else:
        print("FAILURE: Data corruption occurred.")

if __name__ == "__main__":
    run_test()
