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
DIST_DIR = PROJECT_ROOT / "dist" / "Slate"

# Default shared path - can be overridden at runtime if missing
DEFAULT_RELEASE_DIR = Path(r"X:\Extra\Slate_Central\Updates\releases")
DEFAULT_LATEST_POINTER = Path(r"X:\Extra\Slate_Central\Updates\latest.json")

def configured_updates_root():
    """
    The Updates folder under the central server the clients actually read.

    The hardcoded X: default below is a guess about somebody else's drive
    letter, and it disagreed with SERVER_ROOT - so this tool published where no
    client was looking. The configured value is the one the application uses to
    find updates, so it is the one to publish to; the X: path stays only as a
    fallback for a machine with no config.
    """
    try:
        from slate.core.infra.global_config import GlobalConfig
        root = Path(GlobalConfig.server_root())
    except Exception:
        return None
    return root / "Updates" if root.exists() else None


def resolve_paths():
    """Resolve release paths, prompting if defaults are missing."""
    configured = configured_updates_root()
    if configured is not None:
        print(f"📁 Publishing to the configured central server: {configured}")
        return configured / "releases", configured / "latest.json"

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

sys.path.insert(0, str(PROJECT_ROOT))
from slate.core.updater.manifest import (          # noqa: E402
    build as build_manifest,
    manifest_name,
    TARGETS,
)

# Which half of the product this package is for. The application updates its
# client and its server separately and asks for them by name, so a publisher
# that does not say which one it is publishing cannot be found by either.
TARGET = "client"

# Resolved in publish() rather than here: resolve_paths() can prompt, and a
# module that asks the operator a question just for being imported cannot be
# tested, or imported by anything else.
RELEASE_DIR = None
LATEST_POINTER = None

def get_version():
    """Extract version from slate/__init__.py"""
    init_file = PROJECT_ROOT / "slate" / "__init__.py"
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
    global RELEASE_DIR, LATEST_POINTER

    print("🚀 Slate - RELEASE PUBLISHER")
    print("==================================")
    print(f"   target: {TARGET}")

    if RELEASE_DIR is None:
        RELEASE_DIR, LATEST_POINTER = resolve_paths()

    # 1. Validation
    #
    # The two targets are built into different folders, so publishing the server
    # would otherwise zip up the client's dist and label it "server".
    dist_dir = DIST_DIR if TARGET == "client" else PROJECT_ROOT / "dist" / "Slate_Server_Update"
    if not dist_dir.exists():
        print(f"❌ Error: Dist folder not found: {dist_dir}")
        print("   Please run the build for this target first.")
        return

    # 2. Get Version
    version = get_version()
    print(f"📌 Version Detected: {version}")

    # The layout is dictated by the reader, not by what reads nicely in a folder.
    # SidecarEngine resolves a package as <releases>/<package_name> and
    # UpdateChecker opens <releases>/manifest_<target>.json, both flat. Publishing
    # into a per-version subfolder - which this did - puts the manifest somewhere
    # the checker never looks and names a zip the downloader cannot reach, so
    # every release made with this tool was invisible to the application.
    release_folder = RELEASE_DIR
    zip_name = f"Slate_{TARGET.capitalize()}_Update.zip"
    zip_path = release_folder / zip_name
    manifest_path = release_folder / manifest_name(TARGET)

    # 3. Create Release Structure
    if zip_path.exists():
        overwrite = input(f"⚠️ A {TARGET} package is already published. Replace it? (y/n): ")
        if overwrite.lower() != 'y':
            print("Aborted.")
            return

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
    notes_file = dist_dir / "release_notes.txt"
    with open(notes_file, "w", encoding="utf-8") as f:
        f.write(f"Slate - Update v{version}\n")
        f.write("================================\n\n")
        f.write(release_notes)
    
    # 4. Zip Package
    zip_folder(dist_dir, zip_path)
    print(f"✅ Package Created: {zip_name}")
    
    # Cleanup temp notes file
    if notes_file.exists(): os.remove(notes_file)
    
    # 5. Generate Hash
    file_hash = calculate_hash(zip_path)
    print(f"🔐 SHA-256: {file_hash}")
    
    # 6. Create Manifest
    #
    # Written through the shared builder rather than assembled here. This used to
    # put the digest under "sha256", which the sidecar does not read - and because
    # it verified only when the key was present, the package installed without
    # being checked at all. build() refuses to produce a manifest with no hash.
    manifest = build_manifest(
        version=version,
        package_name=zip_name,
        hash_sha256=file_hash,
        target=TARGET,
        release_date=datetime.now().isoformat(),
        critical=False,
        notes=release_notes,      # shown in the update prompt
    )

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
    import argparse

    parser = argparse.ArgumentParser(
        description="Publish a built package where the application looks for it.")
    parser.add_argument("--target", choices=list(TARGETS), default="client",
                        help="Which half of the product this package updates.")
    args = parser.parse_args()
    TARGET = args.target
    publish()
