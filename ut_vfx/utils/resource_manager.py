"""
Resource management utilities for UT_VFX Production tool.
Fixed for PyInstaller --onedir mode with subfolders.
"""
import sys
import os
from pathlib import Path
import logging
from shutil import which

class ResourcePathManager:
    """Manages resource paths for both development and .exe distribution."""
    
    @staticmethod
    def get_resource_path(relative_path: str) -> Path:
        """
        Get absolute path to resource.
        Handles:
        1. Dev mode (running .py)
        2. PyInstaller --onefile (sys._MEIPASS)
        3. PyInstaller --onedir (sys.executable folder)
        """
        try:
            # 1. PyInstaller --onefile mode OR --onedir (PyInstaller 6+ uses _internal)
            # Both set _MEIPASS. In onedir, it points to _internal.
            if hasattr(sys, '_MEIPASS'):
                base_path = Path(sys._MEIPASS)
                
                # Check 1: Nested in 'ut_vfx' (e.g. icons) under internal
                p1 = base_path / "ut_vfx" / relative_path
                if p1.exists(): return p1
                
                # Check 2: Direct under internal (e.g. bundled libs)
                p2 = base_path / relative_path
                if p2.exists(): return p2

                # Check 3: Fallback to Install Root (sibling to .exe)
                # This fixes the issue where bins are in root, but _MEIPASS points to _internal
                exe_root = Path(sys.executable).parent
                p3 = exe_root / relative_path
                if p3.exists(): return p3
                
                # If specifically looking for bin/ffmpeg, checks root/bin/ffmpeg.exe
                if "bin" in relative_path:
                    p4 = exe_root / "bin" / Path(relative_path).name
                    if p4.exists(): return p4

                # Default to _MEIPASS if nothing found, to let caller handle failure
                return p2

            # 2. PyInstaller --onedir mode
            if getattr(sys, 'frozen', False):
                base_path = Path(sys.executable).parent
                # Your batch file puts resources in 'ut_vfx/icons', check that first
                if (base_path / "ut_vfx" / relative_path).exists():
                    return base_path / "ut_vfx" / relative_path
                return base_path / relative_path

            # 3. Development mode
            # Assumes this file is in ut_vfx/utils/
            return Path(__file__).parent.parent / relative_path

        except Exception as e:
            logging.exception(f"Error resolving path: {e}")
            return Path(".") / relative_path
    
    @staticmethod
    def get_icons_dir() -> Path:
        """Get the icons directory path."""
        return ResourcePathManager.get_resource_path("icons")
    
    @staticmethod
    def get_stylesheet() -> str:
        """Get the main stylesheet content."""
        stylesheet_path = ResourcePathManager.get_resource_path("resources/styles.qss")
        if stylesheet_path.exists():
            with open(stylesheet_path, 'r', encoding='utf-8') as f:
                return f.read()
        return ""
    
    @staticmethod
    def get_ffmpeg_path() -> str:
        """Resolve ffmpeg from env override, bundled path, local cache, or system PATH."""
        return ResourcePathManager.resolve_tool_path("ffmpeg")

    @staticmethod
    def get_ffprobe_path() -> str:
        """Resolve ffprobe from env override, bundled path, local cache, or system PATH."""
        resolved = ResourcePathManager.resolve_tool_path("ffprobe", fallback_to_name=False)
        if resolved:
            return resolved

        ffmpeg = ResourcePathManager.get_ffmpeg_path()
        ffmpeg_path = Path(ffmpeg)
        if ffmpeg_path.exists():
            sibling_name = ResourcePathManager._tool_binary_name("ffprobe")
            sibling = ffmpeg_path.parent / sibling_name
            if sibling.exists():
                return str(sibling)

        return "ffprobe"

    @staticmethod
    def resolve_tool_path(tool_name: str, fallback_to_name: bool = True) -> str:
        """Resolve executable path for a CLI tool with predictable fallback order."""
        for candidate in ResourcePathManager._tool_candidates(tool_name):
            try:
                if candidate.exists():
                    return str(candidate)
            except OSError:
                continue

        tool_cmd = ResourcePathManager._tool_binary_name(tool_name)
        if which(tool_cmd):
            return tool_cmd
        if fallback_to_name:
            return tool_cmd
        return ""

    @staticmethod
    def describe_tool_search(tool_name: str) -> str:
        """Human-readable search list for diagnostics."""
        return " | ".join(str(path) for path in ResourcePathManager._tool_candidates(tool_name))

    @staticmethod
    def _tool_binary_name(tool_name: str) -> str:
        if os.name == "nt" and not str(tool_name).lower().endswith(".exe"):
            return f"{tool_name}.exe"
        return tool_name

    @staticmethod
    def _local_tool_dir() -> Path:
        if os.name == "nt":
            root = os.getenv("LOCALAPPDATA")
            if root:
                return Path(root) / "UTVFX" / "bin"
            return Path.home() / "AppData" / "Local" / "UTVFX" / "bin"
        return Path.home() / ".ut_vfx" / "bin"

    @staticmethod
    def _tool_candidates(tool_name: str):
        tool_bin = ResourcePathManager._tool_binary_name(tool_name)
        env_key = f"UTVFX_{tool_name.upper()}_PATH"
        env_value = os.getenv(env_key, "").strip()
        if env_value:
            yield Path(env_value)

        yield ResourcePathManager.get_resource_path(f"bin/{tool_bin}")
        yield ResourcePathManager._local_tool_dir() / tool_bin
