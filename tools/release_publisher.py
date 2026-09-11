import os
import sys
import json
import shutil
import hashlib
import zipfile
from datetime import datetime
from pathlib import Path

# --- CONFIGURATION ---
# --- CONFIGURATION ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist" / "UTVFX"

# Default shared path - can be overridden at runtime if missing
DEFAULT_RELEASE_DIR = Path(r"X:\Extra\UT_Central\Updates\releases")
DEFAULT_LATEST_POINTER = Path(r"X:\Extra\UT_Central\Updates\latest.json")

def resolve_paths():
    """Resolve release paths, prompting if defaults are missing."""
    rel_dir = DEFAULT_RELEASE_DIR
    pointer = DEFAULT_LATEST_POINTER
    
    if not rel_dir.parent.exists(): # Check if Updates folder exists
        print(f"⚠️  Network Drive Path not found: {rel_dir.parent}")
        print("Please enter the path to the 'Updates' folder (or 'q' to quit):")
        while True:
            user_input = input("> ").strip().strip('"')
            if user_input.lower() == 'q':
                sys.exit(0)
            p = Path(user_input)
            if p.exists():
                rel_dir = p / "releases"
                pointer = p / "latest.json"
                break
            print("❌ Path does not exist. Try again.")
            
    return rel_dir, pointer

RELEASE_DIR, LATEST_POINTER = resolve_paths()

def get_version():
    """Extract version from ut_vfx/__init__.py"""
    init_file = PROJECT_ROOT / "ut_vfx" / "__init__.py"
    with open(init_file, "r") as f:
        for line in f:
            if line.startswith("__version__"):
                return line.split('"')[1]
    raise ValueError("Could not find version in __init__.py")

def calculate_hash(file_path):
    """Calculate SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

def zip_folder(folder_path, output_path):
    """Zip the contents of a folder."""
    print(f"📦 Zipping {folder_path}...")
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(folder_path)
                zipf.write(file_path, arcname)

def publish():
    print("🚀 UT_VFX - RELEASE PUBLISHER")
    print("==================================")
    
    # 1. Validation
    if not DIST_DIR.exists():
        print(f"❌ Error: Dist folder not found: {DIST_DIR}")
        print("   Please run 'pyinstaller' build first.")
        return

    # 2. Get Version
    version = get_version()
    print(f"📌 Version Detected: {version}")
    
    release_folder = RELEASE_DIR / f"v{version}"
    # REQUESTED FORMAT: update_vX.X.X.zip
    zip_name = f"update_v{version}.zip"
    zip_path = release_folder / zip_name
    manifest_path = release_folder / "manifest.json"

    # 3. Create Release Structure
    if release_folder.exists():
        overwrite = input(f"⚠️ Release v{version} already exists. Overwrite? (y/n): ")
        if overwrite.lower() != 'y':
            print("Aborted.")
            return
        shutil.rmtree(release_folder)
    
    release_folder.mkdir(parents=True, exist_ok=True)

    # --- RELEASE NOTES INPUT ---
    print("\n📝 Enter Release Notes (Press Enter twice to finish):")
    lines = []
    while True:
        line = input()
        if not line and lines and not lines[-1]: # Stop on empty line if we have content
             break
        if not line: # Allow one empty line for spacing but 2 means stop
             lines.append("")
             continue
        lines.append(line)
    
    release_notes = "\n".join(lines).strip()
    if not release_notes: release_notes = "Regular update."
    
    # Write release notes to file for inclusion in ZIP
    notes_file = DIST_DIR / "release_notes.txt"
    with open(notes_file, "w", encoding="utf-8") as f:
        f.write(f"UT_VFX - Update v{version}\n")
        f.write("================================\n\n")
        f.write(release_notes)
    
    # 4. Zip Package
    zip_folder(DIST_DIR, zip_path)
    print(f"✅ Package Created: {zip_name}")
    
    # Cleanup temp notes file
    if notes_file.exists(): os.remove(notes_file)
    
    # 5. Generate Hash
    file_hash = calculate_hash(zip_path)
    print(f"🔐 SHA-256: {file_hash}")
    
    # 6. Create Manifest
    manifest = {
        "version": version,
        "release_date": datetime.now().isoformat(),
        "package_name": zip_name,
        "sha256": file_hash,
        "critical": False, 
        "notes": release_notes # Store notes in manifest for UI
    }
    
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=4)
    print("📝 Manifest Created.")
    
    # 7. Update Latest Pointer
    pointer_data = {
        "latest_version": version,
        "manifest_path": str(manifest_path),
        "updated_at": datetime.now().isoformat()
    }
    
    # Ensure update root exists
    LATEST_POINTER.parent.mkdir(parents=True, exist_ok=True)
    
    with open(LATEST_POINTER, "w") as f:
        json.dump(pointer_data, f, indent=4)
    
    print("👉 'latest.json' updated.")
    print("\n🎉 RELEASE PUBLISHED SUCCESSFULLY!")

if __name__ == "__main__":
    publish()
