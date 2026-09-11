# Pipeline & Ingestion Configuration

UT_VFX features an extremely robust, multi-threaded ingest engine called `SmartScanWorker` (and `BetaSmartInternalWorker`). This engine allows raw data drives from clients to be automatically converted into standard studio pipelines without manual sorting.

---

## 1. The Smart Ingest Analyzer

When raw files are dragged into the ingest window, the `SmartIngestAnalyzer` evaluates every single file to determine what it is and where it belongs.

### Analysis Logic
The analyzer loads production rules from `ConfigManager.ingest_rules`. For every file, it returns:
- `category`: The logical grouping (e.g., "Plates", "Reference").
- `score`: A confidence percentage (0.0 to 1.0) based on regex and extension mapping.
- `reason`: A debug string explaining the match.

### Template Mapping
Once a file is categorized, the worker attempts to map it to the active Studio Template (defined by the `shot_subs` variable). 
- If `category == "Plates"`, it scans the template for a matching string (e.g., `"01_Plates"`) and dynamically moves the file there.
- If no direct match is found in the template, but the `score` is very high (> 0.8), it will safely create a new folder for that category.
- Fallback: Unrecognized files go to `01_Scan/Unknown`.

---

## 2. Junk Filtering & Safety

To prevent polluting the SAN with temporary OS files, the ingestion workers filter against a hardcoded `IGNORED_FILES` list before analysis begins:
```python
IGNORED_FILES = {
    '.ds_store', 'thumbs.db', 'desktop.ini', 
    '._*',          # AppleDouble resource forks
    '*.tmp', '*.bak', '*.swp',  # Temp/Backup files
    '$recycle.bin', 'system volume information'
}
```

---

## 3. Safe Movement & Verification

UT_VFX treats client data with strict security:

- **Checksum Verification**: When `fast_mode=False`, the system relies on `SafeFileOperations.safe_move_with_verification()`. This generates a checksum of the source file, performs the move, generates a checksum of the destination file, and compares them before deleting the source. If they do not match, the transaction is rolled back and marked as an Error in the SQLite database.
- **Disk Space Prediction**: Before any files are moved, the worker runs a recursive `_calculate_directory_size()` on the source payload and compares it against `psutil.disk_usage()` on the destination drive to ensure at least a 10% safety buffer remains.

---

## 4. Sequence Detection

UT_VFX doesn't just treat image sequences as isolated files. The `SmartScanWorker` utilizes `SequenceDetector` to identify frames belonging to the same sequence (e.g., `shot_v01.0001.exr`, `shot_v01.0002.exr`). This allows the system to group metadata intelligently instead of spamming the database with 50,000 individual file records.

## 5. Dry Run Mode

If the user wants to preview what the `SmartScanWorker` will do without actually moving data, `dry_run=True` can be passed. The worker evaluates the entire logic tree, counts the operations, builds a list of `dry_run_ops`, and emits the `dry_run_data` Signal to the PyQt UI. This allows the user to see a visual diff of the proposed folder structure before committing to the ingestion.
