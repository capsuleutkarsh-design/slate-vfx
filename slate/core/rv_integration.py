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

    def command(self, media_args: List[str]) -> List[str]:
        """
        The RV command line for these media arguments, with the Slate menu.

        It used to go through rvpush as "rvpush -tag slate replace ...", but
        rvpush has no "replace" command (its own usage lists set, merge,
        mu-eval, py-eval, py-exec and url), so with the bundled OpenRV - which
        ships rvpush - nothing opened. The menu is loaded with one Python
        expression, valid whether RV evaluates or executes -pyeval, and the
        folder is written as a Python literal so a quote in it cannot break it.
        """
        # ponytail: one RV window per launch; reuse one window with
        # "rvpush -tag slate set" once that is checked against a running RV.
        core_path = str(Path(__file__).parent).replace("\\", "/")
        pyeval = (f"__import__('sys').path.append({core_path!r}) or "
                  "__import__('rv_plugin').createMode().activate()")
        return [self.rv_executable, *media_args, "-pyeval", pyeval]

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
            cmd = self.command(["-play", media_path])
            logger.info(f"Launching RV: {' '.join(cmd)}")
            subprocess.Popen(cmd, creationflags=0x08000000 if os.name == 'nt' else 0)  # no console
            return True
        except Exception as e:
            logger.error(f"Failed to launch RV: {e}")
            return False
            
    def _generate_rv_session(self, media_paths: List[str], output_rv_path: str) -> bool:
        """
        Write an .rv session that plays media_paths one after another.

        The file written before was not GTO RV could read ("syntax error" at
        line 5), so every playlist - the dashboard's several departments, the
        lineup - failed to open. This is the session format RV writes itself,
        checked by loading it with rvio.
        """
        def quoted(text):
            return '"' + str(text).replace("\\", "/").replace('"', "'") + '"'

        lines = ["GTOa (4)", "",
                 "rv : RVSession (4)", "{", "    session", "    {",
                 '        string viewNode = "defaultSequence"', "    }", "}", ""]
        for i, path in enumerate(media_paths):
            node = f"sourceGroup{i:06d}"
            lines += [f"{node} : RVSourceGroup (1)", "{", "    ui", "    {",
                      f"        string name = {quoted(os.path.basename(str(path)))}", "    }", "}", "",
                      f"{node}_source : RVFileSource (1)", "{", "    media", "    {",
                      f"        string movie = {quoted(path)}", "    }", "}", ""]
        try:
            with open(output_rv_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return True
        except OSError as e:
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

            if self._generate_rv_session(valid_paths, rv_session_path):
                cmd = self.command([rv_session_path])
            else:
                cmd = self.command(["-play"] + valid_paths)

            logger.info(f"Launching RV Playlist with {len(valid_paths)} items")
            subprocess.Popen(cmd, creationflags=0x08000000 if os.name == 'nt' else 0)
            return True
        except Exception as e:
            logger.error(f"Failed to launch RV Playlist: {e}")
            return False
