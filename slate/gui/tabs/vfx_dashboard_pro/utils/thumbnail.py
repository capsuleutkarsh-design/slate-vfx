import os
import re
import subprocess
import shutil
import logging
from pathlib import Path
from slate.core.infra.global_config import GlobalConfig
from slate.utils.resource_manager import ResourcePathManager
from PySide6.QtGui import QImage, QColor
from PySide6.QtCore import QCoreApplication, QThread


logger = logging.getLogger(__name__)

class ThumbnailGenerator:
    def __init__(self, local_cache_dir: str = None):
        """
        Initialize thumbnail generator with FFmpeg and Central Storage
        """
        # 1. Central Storage (Primary)
        self.central_dir = GlobalConfig.central_thumbnails_dir()
        
        # 2. Local Fallback
        if local_cache_dir:
            self.local_dir = Path(local_cache_dir)
        else:
            self.local_dir = GlobalConfig.local_cache_dir() / "thumbnails"
        self.local_dir.mkdir(parents=True, exist_ok=True)

        # 3. Locate FFmpeg
        self.ffmpeg_path = ResourcePathManager.get_ffmpeg_path()
        if not self.ffmpeg_path:
            self.ffmpeg_path = "ffmpeg"

        # 4. Status Images (Paths only, lazy creation)
        self.assets_dir = Path(__file__).resolve().parents[1] / "assets"
        # We prefer local dir for generated placeholders to avoid permission issues in install dir
        self.yellow_path = self.local_dir / "placeholder_yellow.png"
        self.red_path = self.local_dir / "placeholder_red.png"
        
        # self._ensure_status_images() # REMOVED: Lazy load instead

    def _get_status_image(self, path: Path, color_hex: str) -> str:
        """
        Ensure status image exists and return path.
        """
        if path.exists():
            return str(path)
            
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Create 320x180 solid color image
            image = QImage(320, 180, QImage.Format_RGB888)
            image.fill(QColor(color_hex))
            image.save(str(path))
            return str(path)
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning("Failed to create placeholder %s: %s", path, e)
            return str(path) # Return path anyway, caller handles missing file

    def get_yellow_placeholder(self):
        return self._get_status_image(self.yellow_path, '#D9A441')

    def get_red_placeholder(self):
        return self._get_status_image(self.red_path, '#D9635F')

    @staticmethod
    def _normalize_reel_folder(reel: str) -> str:
        reel_text = str(reel or "").strip().replace(" ", "_")
        if reel_text.upper().startswith("RL_"):
            return f"REEL_{reel_text[3:]}"
        if reel_text.upper().startswith("REEL_"):
            return reel_text
        return f"REEL_{reel_text}" if reel_text else "REEL_"

    @staticmethod
    def _dedupe_paths(paths):
        unique = []
        seen = set()
        for path in paths:
            key = str(path).replace("\\", "/").rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _project_root_candidates(self, project_code: str, project_root: str):
        candidates = []
        code_text = str(project_code or "").strip()
        short_code = code_text.split("_", 1)[-1] if "_" in code_text else code_text

        if project_root:
            root = Path(project_root)
            candidates.extend([root, root / code_text])

            # If the root is a drive or share rather than one project, look
            # for a folder inside it whose name carries the project code.
            if root.exists() and root.is_dir():
                try:
                    for child in root.iterdir():
                        if not child.is_dir():
                            continue
                        child_name = child.name.upper()
                        if (code_text and code_text.upper() in child_name) or (
                            short_code and short_code.upper() in child_name
                        ):
                            candidates.append(child)
                except OSError:
                    logger.debug("ThumbnailGen: failed listing project root candidates under %s", root)

        # Fallback when the project root is empty or not specific enough. It
        # was Path("X:/") / code - a drive letter from one studio, compiled in.
        if code_text:
            from slate.core.infra.studio_paths import project_folder

            guess = project_folder(code_text)
            if guess is not None:
                candidates.append(guess)

        return self._dedupe_paths(candidates)

    def resolve_scan_path(self, project_code: str, reel: str, shot: str, project_root: str = "") -> str:
        """
        Build scan path lookup with logging.
        """
        reel_folder = self._normalize_reel_folder(reel)
        code_text = str(project_code or "").strip()
        project_prefix = code_text.split('_', 1)[-1] if "_" in code_text else code_text
        reel_short = reel_folder.replace('REEL_', 'RL_')
        shot_folder = f"{project_prefix}_{reel_short}_{shot}".replace(" ", "_")

        root_candidates = self._project_root_candidates(code_text, project_root)
        strict_candidates = [
            root / "05_Reels" / reel_folder / shot_folder / "01_Scan"
            for root in root_candidates
        ]

        for strict in strict_candidates:
            if strict.exists():
                return str(strict)

        # FUZZY SEARCH (Reel + Shot)
        for root in root_candidates:
            reels_root = root / "05_Reels"
            if not reels_root.exists():
                continue

            found_reel_path = None
            candidate_1 = reels_root / reel_folder
            if candidate_1.exists():
                found_reel_path = candidate_1
            else:
                digits = re.findall(r'\d+', reel_folder) or re.findall(r'\d+', str(reel or ""))
                if digits:
                    number_key = digits[0]
                    try:
                        for r_child in reels_root.iterdir():
                            if r_child.is_dir() and number_key in r_child.name:
                                found_reel_path = r_child
                                break
                    except OSError:
                        logger.debug("ThumbnailGen: failed iterating reel folders under %s", reels_root)

            if not found_reel_path:
                continue

            search_keys = {
                str(shot or "").strip().lower(),
                str(shot or "").replace("SH_", "").strip().lower(),
            }
            normalized_keys = {
                re.sub(r"[\s_\-]+", "", key)
                for key in search_keys
                if key
            }
            try:
                for child in found_reel_path.iterdir():
                    if not child.is_dir():
                        continue
                    name = child.name.lower()
                    normalized_name = re.sub(r"[\s_\-]+", "", name)
                    if any(
                        key and (key in name or key in normalized_name)
                        for key in (search_keys | normalized_keys)
                    ):
                        fuzzy_path = child / "01_Scan"
                        if fuzzy_path.exists():
                            return str(fuzzy_path)
            except OSError:
                continue

        # Return a deterministic strict candidate for debugging/logging purposes.
        if strict_candidates:
            return str(strict_candidates[0])
        if project_root:
            fallback_root = Path(project_root)
        else:
            # Was Path("X:/") / code - a drive letter from one studio. With no
            # configured root there is no path worth returning, so the caller
            # gets an empty string and can say "not found" rather than print a
            # location that never existed.
            from slate.core.infra.studio_paths import project_folder

            guess = project_folder(code_text)
            if guess is None:
                return ""
            fallback_root = guess
        return str(fallback_root / "05_Reels" / reel_folder / shot_folder / "01_Scan")
    
    def find_first_frame(self, scan_path: str) -> str:
        """Find first supported media file (Recursive search)."""
        if not os.path.exists(scan_path):
            # print(f"[Thumb] Scan Path Invalid: {scan_path}")
            return ""
            
        from slate.utils.media_capabilities import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
        supported_exts = set(IMAGE_EXTENSIONS) | set(VIDEO_EXTENSIONS)
        
        # Method 1: Recursive Search (Depth limited to avoid deep trees)
        # Search scan_path and immediate subfolders
        candidates = []
        
        try:
             # Look in root
            for f in os.listdir(scan_path):
                full = os.path.join(scan_path, f)
                if os.path.isfile(full):
                    if os.path.splitext(f)[1].lower() in supported_exts:
                        candidates.append(full)
                elif os.path.isdir(full):
                    # Look one level down (e.g. EXR, DPX, Mov)
                    for sub in os.listdir(full):
                        sub_full = os.path.join(full, sub)
                        if os.path.isfile(sub_full):
                             if os.path.splitext(sub)[1].lower() in supported_exts:
                                candidates.append(sub_full)
                                
            if candidates:
                # Sort to get first frame (e.g. 1001)
                found = sorted(candidates)[0]
                return found
                
        except OSError as e:
            logger.debug("ThumbnailGen: failed scanning %s: %s", scan_path, e)
            
        return ""

    def generate_with_ffmpeg(self, source_path: str, dest_path: str) -> bool:
        """
        Convert frame to JPG using FFmpeg.
        """
        if self._is_gui_thread():
            logger.warning("ThumbnailGen: blocking ffmpeg call prevented on GUI thread for %s", source_path)
            return False

        try:
            # -y: overwrite, -i: input, -vf: scale, -vframes 1, -q:v 5
            cmd = [
                self.ffmpeg_path, '-y', '-i', source_path,
                '-vf', 'scale=300:-1', '-vframes', '1', '-q:v', '5',
                dest_path
            ]
            creationflags = 0x08000000 if os.name == 'nt' else 0
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                creationflags=creationflags,
                timeout=30,
            )
            return True
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            logger.warning("FFmpeg thumbnail generation failed for %s: %s", source_path, e)
            return False

    @staticmethod
    def _is_gui_thread() -> bool:
        app = QCoreApplication.instance()
        if app is None:
            return False
        gui_thread = app.thread()
        return gui_thread is not None and QThread.currentThread() == gui_thread

    def get_or_create_thumbnail(self, project_code: str, reel: str, shot: str, project_root: str = "") -> str:
        """
        Get cached thumbnail or generate new one.
        Checks: Central Cache -> Local Cache -> Generate
        """
        def safe_segment(value: str) -> str:
            text = str(value or "").strip()
            text = text.replace("\\", "_").replace("/", "_")
            text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
            return text or "unknown"

        # 1. Build new structured cache path:
        #    .../Thumbnails/dashboard/<project>/<reel>/<shot>/thumb.jpg
        safe_project = safe_segment(project_code)
        safe_reel = safe_segment(reel.replace('REEL_', 'RL_'))
        safe_shot = safe_segment(shot)
        filename = f"{safe_project}_{safe_reel}_{safe_shot}.jpg"

        central_path = self.central_dir / "dashboard" / safe_project / safe_reel / safe_shot / "thumb.jpg"
        legacy_central_path = self.central_dir / filename

        # 2. Reuse existing central cache (new path preferred, legacy migrated on-demand)
        if central_path.exists() and central_path.stat().st_size > 0:
            return str(central_path)
        if legacy_central_path.exists() and legacy_central_path.stat().st_size > 0:
            try:
                central_path.parent.mkdir(parents=True, exist_ok=True)
                import threading
                threading.Thread(target=shutil.copy2, args=(legacy_central_path, central_path), daemon=True).start()
                return str(legacy_central_path)
            except Exception:
                return str(legacy_central_path)
            
        # 3. Find Source Material
        scan_path = self.resolve_scan_path(project_code, reel, shot, project_root)
        source_frame = self.find_first_frame(scan_path)
        
        if not source_frame:
            return self.get_red_placeholder() # Return RED if source missing

        if self._is_gui_thread():
            logger.warning(
                "Thumbnail generation deferred because caller is on GUI thread (project=%s reel=%s shot=%s).",
                project_code,
                reel,
                shot,
            )
            return self.get_yellow_placeholder()
             
        # 4. Generate -> Central
        try:
            central_path.parent.mkdir(parents=True, exist_ok=True)
            if self.generate_with_ffmpeg(source_frame, str(central_path)):
                return str(central_path)
        except OSError:
            logger.debug("ThumbnailGen: failed preparing central thumbnail path %s", central_path)
             
        # 5. Fallback: Generate -> Local
        local_path = self.local_dir / "dashboard" / safe_project / safe_reel / safe_shot / "thumb.jpg"
        legacy_local_path = self.local_dir / filename

        if local_path.exists() and local_path.stat().st_size > 0:
            return str(local_path)
        if legacy_local_path.exists() and legacy_local_path.stat().st_size > 0:
            if self._is_gui_thread():
                return str(legacy_local_path)
            try:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_local_path, local_path)
                return str(local_path)
            except OSError:
                return str(legacy_local_path)
            
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if self.generate_with_ffmpeg(source_frame, str(local_path)):
            return str(local_path)
            
        return self.get_red_placeholder() # Return RED if generation failed
