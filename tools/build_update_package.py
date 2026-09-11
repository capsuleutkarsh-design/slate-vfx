import os
import sys
import shutil
import hashlib
import json
import subprocess
import argparse
from pathlib import Path

def generate_file_hash(filepath):
    """Generate SHA-256 hash for a file."""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def build_single_target(target="vfx", project_root=None):
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    target = "vfx" if target == "client" else target
    print("=" * 70)
    print(f"  BUILDING UPDATE PACKAGE [{target.upper()}]")
    print("=" * 70)

    if target in ("vfx", "ops"):
        print(f"\nStep 1: Running ONEDIR build for {target.upper()}...")
        result = subprocess.run([sys.executable, "tools/build_pipeline.py", "--mode", "onedir"])
        if result.returncode != 0:
            print("ERROR: Build pipeline failed.")
            sys.exit(1)
            
        dist_dir = project_root / "dist" / "Slate"
        zip_filename = f"UT_{target.upper()}_Update"
    elif target == "server":
        print("\nStep 1: Running Server build...")
        subprocess.run([sys.executable, "-m", "PyInstaller", "UT_Server.spec", "--noconfirm"])
        
        dist_dir = project_root / "dist" / "UT_Server_Update"
        if dist_dir.exists():
            shutil.rmtree(dist_dir)
        dist_dir.mkdir(parents=True)
        
        server_exe = project_root / "dist" / "UT_Server.exe"
        if not server_exe.exists():
            print("ERROR: UT_Server.exe not found. Did the build fail?")
            sys.exit(1)
        shutil.copy2(server_exe, dist_dir / "UT_Server.exe")
        
        bin_dir = project_root / "slate_server" / "bin"
        if bin_dir.exists():
            shutil.copytree(bin_dir, dist_dir / "bin")
            
        zip_filename = "UT_Server_Update"
    else:
        print(f"ERROR: Unknown target '{target}'")
        sys.exit(1)

    if not dist_dir.exists():
        print(f"ERROR: {dist_dir} directory not found after build.")
        sys.exit(1)

    print("\nStep 2: Preparing Update Manifest (Internal)...")
    update_files = []
    for root, dirs, files in os.walk(dist_dir):
        for file in files:
            rel_path = os.path.relpath(os.path.join(root, file), dist_dir)
            update_files.append(rel_path.replace("\\", "/"))

    internal_manifest_path = dist_dir / "update_manifest.json"
    with open(internal_manifest_path, "w", encoding="utf-8") as f:
        json.dump({"files": update_files}, f, indent=4)

    print("\nStep 3: Zipping the application...")
    releases_dir = project_root / "releases"
    releases_dir.mkdir(exist_ok=True)
    
    zip_filepath = releases_dir / f"{zip_filename}.zip"
    if zip_filepath.exists():
        os.remove(zip_filepath)
        
    shutil.make_archive(str(releases_dir / zip_filename), 'zip', dist_dir)
    
    print("\nStep 4: Generating Cryptographic Hash (SHA-256)...")
    file_hash = generate_file_hash(zip_filepath)
    
    external_manifest = {
        "version": "latest",
        "target": target,
        "hash_sha256": file_hash,
        "package_name": f"{zip_filename}.zip"
    }
    
    external_manifest_path = releases_dir / f"manifest_{target}.json"
    with open(external_manifest_path, "w", encoding="utf-8") as f:
        json.dump(external_manifest, f, indent=4)
        
    print("\n" + "=" * 70)
    print(f"  [SUCCESS] Update Package created for {target.upper()}!")
    print("=" * 70)
    print(f"Package: {zip_filepath}")
    print(f"Manifest: {external_manifest_path}")
    print(f"SHA-256: {file_hash}")

def build_update_package(target="all"):
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)

    if target == "all":
        for t in ["vfx", "ops", "server"]:
            build_single_target(t, project_root)
    else:
        build_single_target(target, project_root)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target", 
        choices=["all", "vfx", "ops", "server", "client"], 
        default="all", 
        help="Build target (all, vfx, ops, server, client)"
    )
    args = parser.parse_args()
    build_update_package(args.target)
