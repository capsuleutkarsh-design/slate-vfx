import os
import sys
import shutil
import hashlib
import json
import subprocess
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from slate.core.updater.manifest import (          # noqa: E402
    build as build_manifest,
    manifest_name,
    releases_dir as releases_dir_for,
)


def generate_file_hash(filepath):
    """Generate SHA-256 hash for a file."""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def publish(zip_filepath, manifest_path, project_root):
    """
    Copy the finished package to where the application looks for it.

    Building into ./releases and stopping there is why nothing has ever been
    offered as an update: the checker reads SERVER_ROOT/Updates/releases and
    nothing was ever putting anything in it. The build is kept as well, so there
    is still a local copy to inspect.

    Returns the published folder, or None with an explanation printed - a
    central folder that is not mounted is a normal thing on a build machine and
    should not fail a build that otherwise succeeded.
    """
    try:
        from slate.core.infra.global_config import GlobalConfig
        server_root = Path(GlobalConfig.server_root())
    except Exception as exc:
        print(f"  [publish] Could not read SERVER_ROOT from config: {exc}")
        return None

    if not server_root.exists():
        print(f"  [publish] SERVER_ROOT does not exist: {server_root}")
        return None

    destination = releases_dir_for(server_root / "Updates")
    try:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(zip_filepath, destination / zip_filepath.name)
        shutil.copy2(manifest_path, destination / manifest_path.name)
    except OSError as exc:
        print(f"  [publish] Could not write to {destination}: {exc}")
        return None

    return destination

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
        # Both the VFX and the Ops builds come out of the same dist/Slate folder
        # above, so they are the same package under two names. The application
        # asks for one "client" update, so that is what gets published - running
        # this for vfx and then for ops republishes the same thing rather than
        # leaving two manifests disagreeing about which is current.
        zip_filename = "Slate_Client_Update"
    elif target == "server":
        print("\nStep 1: Running Server build...")
        # Same move as above: the specs live in deployment/ now.
        server_spec = next(
            (p for p in (Path("deployment") / "Slate_Server.spec",
                         Path("Slate_Server.spec")) if p.exists()), None)
        if server_spec is None:
            print("ERROR: Slate_Server.spec not found in deployment/ or the project root.")
            sys.exit(1)
        subprocess.run([sys.executable, "-m", "PyInstaller", str(server_spec), "--noconfirm"])
        
        dist_dir = project_root / "dist" / "Slate_Server_Update"
        if dist_dir.exists():
            shutil.rmtree(dist_dir)
        dist_dir.mkdir(parents=True)
        
        server_exe = project_root / "dist" / "Slate_Server.exe"
        if not server_exe.exists():
            print("ERROR: Slate_Server.exe not found. Did the build fail?")
            sys.exit(1)
        shutil.copy2(server_exe, dist_dir / "Slate_Server.exe")
        
        bin_dir = project_root / "slate_server" / "bin"
        if bin_dir.exists():
            shutil.copytree(bin_dir, dist_dir / "bin")
            
        zip_filename = "Slate_Server_Update"
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
    
    # The build knows three targets because it produces three executables; the
    # application knows two, because a workstation updates its client and the
    # server machine updates its server. The manifest is named for what the
    # application asks for - it looks for manifest_client.json, and a file
    # called manifest_vfx.json is simply never found.
    app_target = "server" if target == "server" else "client"

    external_manifest = build_manifest(
        version="latest",
        package_name=f"{zip_filename}.zip",
        hash_sha256=file_hash,
        target=app_target,
        built_from=target,
    )

    external_manifest_path = releases_dir / manifest_name(app_target)
    with open(external_manifest_path, "w", encoding="utf-8") as f:
        json.dump(external_manifest, f, indent=4)

    print("\n" + "=" * 70)
    print(f"  [SUCCESS] Update Package created for {target.upper()}!")
    print("=" * 70)
    print(f"Package: {zip_filepath}")
    print(f"Manifest: {external_manifest_path}")
    print(f"SHA-256: {file_hash}")

    published = publish(zip_filepath, external_manifest_path, project_root)
    if published:
        print(f"Published: {published}")
    else:
        print("Not published - built locally only. See the message above.")

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
