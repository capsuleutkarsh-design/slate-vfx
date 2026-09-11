import os
import subprocess
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

class RVLauncher:
    """
    Handles launching and communicating with OpenRV for media playback.
    """
    
    def __init__(self):
        self.rv_executable = self._find_rv_executable()
        self.rvpush_executable = self._find_rvpush_executable()
        
    def _find_rvpush_executable(self) -> str:
        """Locate rvpush.exe for single-window mode control."""
        if self.rv_executable != "rv":
            rvpush = Path(self.rv_executable).parent / "rvpush.exe"
            if rvpush.exists():
                return str(rvpush)
        return "rvpush"

    def _find_rv_executable(self) -> str:
        """Locate the RV executable in common install paths or environment variables."""
        # Check environment variable first
        env_rv = os.environ.get("RV_PATH")
        if env_rv and os.path.exists(env_rv):
            return env_rv
            
        # Check local bundle paths (Portable RV bundled with our software)
        try:
            import sys
            base_dir = Path(__file__).parent.parent
            
            bundled_paths = [
                base_dir / "bin" / "OpenRV" / "bin" / "rv.exe",        # Inside slate/bin
                base_dir.parent / "OpenRV" / "bin" / "rv.exe",         # Next to slate
                base_dir.parent / "bin" / "OpenRV" / "bin" / "rv.exe", # In parent bin dir
            ]
            
            # If frozen via PyInstaller, look next to the actual .exe
            if getattr(sys, 'frozen', False):
                exe_dir = Path(sys.executable).parent
                bundled_paths.extend([
                    exe_dir / "OpenRV" / "bin" / "rv.exe",
                    exe_dir / "bin" / "OpenRV" / "bin" / "rv.exe",
                ])

            for b_path in bundled_paths:
                if b_path.exists():
                    return str(b_path)
        except Exception:
            pass
            
        # Common Windows paths for OpenRV
        prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        paths_to_check = [
            os.path.join(prog_files, "OpenRV", "bin", "rv.exe"),
            os.path.join(prog_files, "Autodesk", "RV", "bin", "rv.exe"),
        ]
        
        for path in paths_to_check:
            if os.path.exists(path):
                return path
                
        # Assume it's in PATH
        return "rv"
        
    def is_rv_available(self) -> bool:
        """Check if RV can be launched."""
        if self.rv_executable != "rv":
            return True
            
        # Try to run `rv -version` or similar just to check if it's in PATH
        try:
            subprocess.run(["rv", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def media_exists(media_path: str) -> bool:
        """
        Whether RV has something to open.

        An image sequence is passed as a printf pattern (shot.%04d.exr), which
        never exists as a file of its own - checking it with os.path.exists
        rejects every EXR sequence in the project, which is most of them. For a
        pattern, the folder holding the frames is what has to be there.
        """
        if not media_path:
            return False
        if "%" in str(media_path) or "#" in str(media_path):
            return os.path.isdir(os.path.dirname(str(media_path)))
        return os.path.exists(media_path)

    def launch_media(self, media_path: str) -> bool:
        """Launch a single media file or sequence in RV."""
        if not self.media_exists(media_path):
            logger.error(f"Cannot launch RV: Media path does not exist {media_path}")
            return False
            
        try:
            creationflags = 0x08000000 if os.name == 'nt' else 0 # DETACHED_PROCESS
            
            core_path = str(Path(__file__).parent).replace("\\", "/")
            pyeval_cmd = f"import sys; sys.path.append('{core_path}'); import rv_plugin; m = rv_plugin.createMode(); m.activate()"
            
            # Use rvpush if available to avoid opening multiple windows
            if self.rvpush_executable != "rvpush" and os.path.exists(self.rvpush_executable):
                cmd = [self.rvpush_executable, "-tag", "slate", "replace", media_path, "-pyeval", pyeval_cmd]
            else:
                # -y flag skips confirmation dialogues, -play auto-plays
                cmd = [self.rv_executable, "-play", media_path, "-pyeval", pyeval_cmd]
            
            logger.info(f"Launching RV: {' '.join(cmd)}")
            subprocess.Popen(cmd, creationflags=creationflags)
            return True
        except Exception as e:
            logger.error(f"Failed to launch RV: {e}")
            return False
            
    def _generate_rv_session(self, media_paths: List[str], output_rv_path: str) -> bool:
        """Generate a basic .rv GTO session file for a playlist of media paths."""
        try:
            # A very basic RV session file structure
            lines = [
                'GTOa (3)',
                '',
                'rv : RVSession (1)',
                '{',
                '    int fps = 24',
                '    int realtime = 1',
                '}',
                ''
            ]
            
            source_nodes = []
            for i, path in enumerate(media_paths):
                # RV expects forward slashes
                safe_path = path.replace("\\", "/")
                node_name = f"sourceGroup{i:03d}"
                source_nodes.append(node_name)
                
                lines.extend([
                    f'{node_name} : RVSourceGroup (1)',
                    '{',
                    f'    string ui : name = "{os.path.basename(safe_path)}"',
                    '}',
                    '',
                    f'{node_name}_source : RVFileSource (1)',
                    '{',
                    f'    string media : movie = "{safe_path}"',
                    '}',
                    ''
                ])
                
            # Define default sequence layout
            lines.extend([
                'defaultLayout : RVLayoutGroup (1)',
                '{',
                '    int mode = 1', # 1 = stack/sequence
                '}',
                ''
            ])
            
            # Write to file
            with open(output_rv_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
                
            return True
        except Exception as e:
            logger.error(f"Failed to generate RV session file: {e}")
            return False

    def launch_playlist(self, media_paths: List[str]) -> bool:
        """Launch multiple media files as a playlist in RV using an .rv session file."""
        if not media_paths:
            return False
            
        valid_paths = [p for p in media_paths if self.media_exists(p)]
        if not valid_paths:
            return False
            
        try:
            # Write a temporary .rv file
            from slate.core.infra.global_config import GlobalConfig
            cache_dir = GlobalConfig.local_cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            rv_session_path = str(cache_dir / "temp_playlist.rv")
            
            creationflags = 0x08000000 if os.name == 'nt' else 0
            
            core_path = str(Path(__file__).parent).replace("\\", "/")
            pyeval_cmd = f"import sys; sys.path.append('{core_path}'); import rv_plugin; m = rv_plugin.createMode(); m.activate()"
            
            if not self._generate_rv_session(valid_paths, rv_session_path):
                # Fallback to direct launch
                if self.rvpush_executable != "rvpush" and os.path.exists(self.rvpush_executable):
                    cmd = [self.rvpush_executable, "-tag", "slate", "replace"] + valid_paths + ["-pyeval", pyeval_cmd]
                else:
                    cmd = [self.rv_executable, "-play"] + valid_paths + ["-pyeval", pyeval_cmd]
            else:
                if self.rvpush_executable != "rvpush" and os.path.exists(self.rvpush_executable):
                    cmd = [self.rvpush_executable, "-tag", "slate", "replace", rv_session_path, "-pyeval", pyeval_cmd]
                else:
                    cmd = [self.rv_executable, rv_session_path, "-pyeval", pyeval_cmd]
                
            logger.info(f"Launching RV Playlist with {len(valid_paths)} items")
            subprocess.Popen(cmd, creationflags=creationflags)
            return True
        except Exception as e:
            logger.error(f"Failed to launch RV Playlist: {e}")
            return False
