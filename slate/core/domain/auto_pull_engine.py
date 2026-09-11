"""
Auto-Pull Engine for VFX Review

Automatically detects scan and render files from project folder structure.
Handles multiple naming conventions and folder layouts.
"""

from pathlib import Path
from typing import Optional
import logging
import re  # Required for version detection regex

from .review_shot import ReviewShot, ShotStatus
from ...utils.sequence_utils import get_sequence_info, get_first_frame_path

logger = logging.getLogger(__name__)


class AutoPullEngine:
    """
    Intelligent auto-detection of scan/render pairs
    
    Supports standard VFX folder structures:
    - PROJECT/SEQ###/Shot_###/scan/
    - PROJECT/SEQ###/Shot_###/render/
    - PROJECT/SEQ###/Shot_###/plates/
    - PROJECT/SEQ###/Shot_###/comp/
    """
    
    # Patterns to search for scan (plates)
    SCAN_PATTERNS = [
        ('01_Scan/EXR', ['*.exr', '*.dpx']),            # HIGH PRIORITY: Explicit EXR folder
        ('01_Scan', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg']),  # USER'S STRUCTURE
        ('scan', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg']),
        ('plates', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg']),
        ('source', ['*.exr', '*.dpx']),
        ('src', ['*.exr', '*.dpx']),
    ]
    VIDEO_PATTERNS = ['*.mov', '*.mp4', '*.mkv', '*.avi', '*.mxf', '*.webm']
    
    # Patterns to search for render (comp output)
    RENDER_PATTERNS = [
        ('08_Output/PREP/EXR', ['*.exr'] + VIDEO_PATTERNS),                          # HIGH PRIORITY: Exact user path
        ('08_Output/PREP', ['*.exr', '*.dpx', '*.tif', '*.tiff'] + VIDEO_PATTERNS), # High Priority Prep
        ('07_Comp', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg'] + VIDEO_PATTERNS),
        ('08_Output', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg'] + VIDEO_PATTERNS),
        ('05_Prep', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg'] + VIDEO_PATTERNS),
        ('render', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg'] + VIDEO_PATTERNS),
        ('comp', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg'] + VIDEO_PATTERNS),
        ('output', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg'] + VIDEO_PATTERNS),
        ('out', ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg'] + VIDEO_PATTERNS),
    ]
    
    def __init__(self):
        self.shots_found = 0
        self.shots_missing_scan = 0
        self.shots_missing_render = 0
    
    def detect_shots(self, project_path: Path) -> list[ReviewShot]:
        """
        Scan project folder for shots - ROBUST RECURSIVE MODE
        
        Identifies a folder as a "Shot" if it contains standard subfolders
        like '01_Scan', '07_Comp', 'scan', 'plates', etc.
        
        Args:
            project_path: Root search directory
        
        Returns:
            List of ReviewShot objects
        """
        import os
        logger.info(f"Scanning project recursively: {project_path}")
        
        shots = []
        self.shots_found = 0
        self.shots_missing_scan = 0
        self.shots_missing_render = 0
        
        if not project_path.exists():
            logger.error(f"Project path does not exist: {project_path}")
            return shots
            
        # Define markers that identify a folder as a "Shot folder"
        # If a folder has any of these subfolders, we assume it's a shot
        shot_markers = {
             '01_scan', 'scan', 'plates', 'source', 'src',
             '07_comp', '08_output', 'render', 'comp', 'output'
        }
        
        # Traverse entire tree
        for root, dirs, files in os.walk(str(project_path)):
            # Skip hidden and cache folders
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['sys', 'history', 'versions']]
            
            root_path = Path(root)
            
            # Check if current folder contains any shot markers
            # We look at 'dirs' which lists immediate subfolders
            has_marker = any(d.lower() in shot_markers for d in dirs)
            
            if has_marker:
                # This 'root_path' is likely a SHOT folder
                # e.g. root_path = .../sq010_sh010/  and it contains '01_Scan'
                
                # Infer sequence name from parent folder name
                sequence_name = root_path.parent.name
                
                shot = self._process_shot_folder(root_path, sequence_name, project_path.name)
                if shot:
                    shots.append(shot)
                    self.shots_found += 1
                    
                # Setup optimization: don't drill deeper into a known Shot folder 
                # looking for *more* shots (unless shots are nested, which is rare)
                # But we MUST allow standard os.walk to continue in case there are peer folders
                
                # Actually, to prevent "01_Scan" being detected as a shot if it contains "render" (unlikely)
                # we can clear 'dirs' here to stop walking deeper into THIS branch?
                # No, because _process_shot_folder handles the internals.
                # But we should prevent a subfolder like '01_Scan' from being detected as a shot itself
                # if IT contains a marker (unlikely recursively).
                
                # Risky edge case: what if '01_Scan' has a folder named 'scan' inside?
                # Then '01_Scan' would be detected as a shot.
                # To prevent this, strict check: name of shot folder shouldn't be a marker itself
                if root_path.name.lower() in shot_markers:
                    # This is a false positive (we are inside the shot structure already)
                    # e.g. we are at .../Shot/01_Scan/ and it has a 'plates' folder?
                    if shot:
                        # We already added it, remove it?
                        # No, just don't add it in the first place.
                        # Wait, logic above added it. Let's refine.
                        pass
        
        # Filtering pass: Remove duplicates or nested detections
        # If we have Shot A and Shot A/Child, usually only A is real.
        # Simple heuristic: Shot name should not be a marker name.
        real_shots = []
        for s in shots:
            if s.name.lower() not in shot_markers:
                real_shots.append(s)
            else:
                self.shots_found -= 1 # Correct stats
                
        shots = real_shots
        
        logger.info(f"Found {self.shots_found} shots")
        return shots
    
    def _process_shot_folder(self, shot_folder: Path, sequence: str, project_name: str = "") -> Optional[ReviewShot]:
        """
        Process single shot folder to detect files
        
        Args:
            shot_folder: Path to shot directory
            sequence: Sequence name
            project_name: Current project name
        
        Returns:
            ReviewShot object or None
        """
        shot_name = shot_folder.name
        logger.debug(f"Processing shot: {sequence}/{shot_name}")
        
        # Try to find scan
        scan_info = self._find_files(shot_folder, self.SCAN_PATTERNS, 'scan')
        if scan_info:
            logging.debug(f"Found SCAN for {shot_name}: Range {scan_info.get('frame_range')} | Path: {scan_info.get('full_path')}")
            
        # Try to find render
        render_info = self._find_files(shot_folder, self.RENDER_PATTERNS, 'render')
        if render_info:
            logging.debug(f"Found RENDER for {shot_name}: Range {render_info.get('frame_range')} | Path: {render_info.get('full_path')}")
        
        # Skip if neither found
        if not scan_info and not render_info:
            logger.warning(f"No files found for {shot_name}")
            return None
        
        # Track missing files
        if not scan_info:
            self.shots_missing_scan += 1
        if not render_info:
            self.shots_missing_render += 1
        
        # Extract metadata from first available source
        meta = scan_info if scan_info else render_info
        
        # Create shot object
        shot = ReviewShot(
            id=f"{sequence}_{shot_name}",
            name=shot_name,
            sequence=sequence,
            project_name=project_name,
            scan_path=self._get_full_path(shot_folder, scan_info) if scan_info else None,
            render_path=self._get_full_path(shot_folder, render_info) if render_info else None,
            frame_range=meta.get('frame_range'),
            format=meta.get('format', 'unknown'),
            status=ShotStatus.PENDING
        )
        
        logger.info(f"Created shot: {shot.name} [Scan: {shot.has_scan()}, Render: {shot.has_render()}]")
        
        return shot
    
    def _find_files(self, shot_folder: Path, patterns: list[tuple], file_type: str) -> Optional[dict]:
        """
        Find files matching patterns
        
        Args:
            shot_folder: Shot directory
            patterns: List of (subfolder_name, [extensions]) tuples
            file_type: 'scan' or 'render' for logging
        
        Returns:
            Dict with file info or None
        """
        candidates = []

        for pattern_index, (subfolder_name, extensions) in enumerate(patterns):
            # 1. Check immediate subfolder (e.g. SHOT/01_Scan)
            target_path = shot_folder / subfolder_name
            
            if target_path.exists():
                found_info = self._search_deep_for_sequence(target_path, subfolder_name, extensions, file_type)
                if found_info:
                    found_info['pattern_priority'] = len(patterns) - pattern_index
                    candidates.append(found_info)

            # 2. If not found, check immediate children for the category folder
            # This handles: SHOT/footage/01_Scan/...
            try:
                child_dirs = [child for child in shot_folder.iterdir() if child.is_dir()]
            except OSError as e:
                logger.debug(f"Could not inspect child dirs for {shot_folder}: {e}")
                child_dirs = []

            for child in child_dirs:
                if child.name not in [subfolder_name, 'sys', 'history']:
                    deep_target = child / subfolder_name
                    if deep_target.exists():
                        found_info = self._search_deep_for_sequence(deep_target, subfolder_name, extensions, file_type)
                        if found_info:
                            found_info['pattern_priority'] = len(patterns) - pattern_index
                            candidates.append(found_info)
        
        # Fallback pass for renders: scan the full shot tree for any valid render media.
        if not candidates and file_type == 'render':
            fallback = self._find_render_fallback(shot_folder)
            if fallback:
                logger.info(f"Render fallback resolved for {shot_folder.name}: {fallback.get('pattern')}")
                return fallback

        if not candidates:
            return None

        candidates.sort(key=lambda i: self._candidate_sort_key(i, file_type), reverse=True)
        best = candidates[0]

        if len(candidates) > 1:
            v_best = self._extract_version(best)
            logger.info(
                f"Smart selection picked {best.get('pattern')} (v{v_best}) from {len(candidates)} {file_type} candidates"
            )

        return best

    def _candidate_sort_key(self, info: dict, file_type: str) -> tuple:
        """Ranking key for choosing the most relevant scan/render candidate."""
        pattern_priority = info.get('pattern_priority', 0)
        keyword_score = self._path_keyword_score(info.get('full_path'), file_type)
        media_score = self._media_priority_score(info, file_type)
        version = self._extract_version(info)
        frame_count = int(info.get('frame_count') or 0)
        mtime = float(info.get('mtime') or 0.0)
        sequence_bonus = 1 if frame_count > 1 else 0
        return (pattern_priority + keyword_score + media_score, version, sequence_bonus, frame_count, mtime)

    def _extract_version(self, info: dict) -> int:
        """Extract highest v### token found in candidate path."""
        path_str = str(info.get('full_path', ''))
        matches = re.findall(r'[vV](\d+)', path_str)
        if not matches:
            return 0
        try:
            return max(int(m) for m in matches)
        except (TypeError, ValueError):
            return 0

    def _path_keyword_score(self, path_value, file_type: str) -> int:
        """Keyword scoring to prioritize likely production folders."""
        path_str = str(path_value or "").replace("\\", "/").lower()
        score = 0

        if file_type == 'render':
            positive = {
                '/08_output/': 40,
                '/08_output/prep/': 20,
                '/08_output/final/': 18,
                '/prep/': 30,
                '/07_comp/': 26,
                '/comp/': 20,
                '/slapcomp/': 18,
                '/precomp/': 16,
                '/render/': 15,
                '/output/': 12,
                '/final/': 10,
            }
            negative = {
                '/01_scan/': -28,
                '/scan/': -22,
                '/plates/': -20,
                '/plate/': -18,
                '/source/': -16,
                '/src/': -12,
                '/proxy/': -16,
                '/preview/': -12,
                '/thumb/': -10,
                '/thumbnail/': -10,
                '/cache/': -8,
            }
        else:
            positive = {
                '/01_scan/': 30,
                '/scan/': 20,
                '/plates/': 18,
                '/plate/': 16,
                '/source/': 10,
            }
            negative = {
                '/proxy/': -12,
                '/preview/': -8,
                '/cache/': -8,
            }

        for token, value in positive.items():
            if token in path_str:
                score += value
        for token, value in negative.items():
            if token in path_str:
                score += value

        return score

    def _media_priority_score(self, info: dict, file_type: str) -> int:
        """Prefer likely review-ready media for render selection."""
        if file_type != 'render':
            return 0

        fmt = str(info.get('format') or "").lower()
        if fmt in {"mp4", "mov", "mkv", "avi", "mxf", "webm"}:
            return 12
        if fmt in {"exr", "dpx", "tif", "tiff"}:
            return 5
        return 0

    def _find_render_fallback(self, shot_folder: Path) -> Optional[dict]:
        """Last-resort render detection across the full shot tree."""
        import os

        fallback_patterns = ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg'] + self.VIDEO_PATTERNS
        candidates = []
        scan_like_dirs = {"01_scan", "scan", "plates", "plate", "source", "src"}

        for current_root, dirs, _ in os.walk(str(shot_folder)):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['sys', 'history', 'versions']]

            current_path = Path(current_root)
            root_tokens = {part.lower() for part in current_path.parts}
            if root_tokens.intersection(scan_like_dirs):
                continue

            info = self._check_path_for_sequence(current_path, "fallback", fallback_patterns, "render")
            if info:
                info.setdefault('pattern_priority', 0)
                candidates.append(info)

        if not candidates:
            return None

        candidates.sort(key=lambda i: self._candidate_sort_key(i, 'render'), reverse=True)
        return candidates[0]

    def _search_deep_for_sequence(self, root_path: Path, subfolder_name: str, extensions: list, file_type: str) -> Optional[dict]:
        """
        Recursively walk directory and choose best candidate.
        """
        import os

        candidates = []

        # 1. Check root first
        info = self._check_path_for_sequence(root_path, subfolder_name, extensions, file_type)
        if info:
            candidates.append(info)

        # 2. Walk subdirectories
        for current_root, dirs, files in os.walk(str(root_path)):
            # Skip hidden folders
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            current_path = Path(current_root)
            if current_path == root_path:
                continue # Already checked root
                
            info = self._check_path_for_sequence(current_path, subfolder_name, extensions, file_type)
            if info:
                candidates.append(info)

        if not candidates:
            return None

        candidates.sort(key=lambda i: self._candidate_sort_key(i, file_type), reverse=True)
        return candidates[0]

    def _check_path_for_sequence(self, path: Path, subfolder_name: str, extensions: list, file_type: str) -> Optional[dict]:
        """Helper to check a specific path for valid sequence"""
        try:
            if not path.exists():
                return None
                
            seq_info = get_sequence_info(path, extensions)
            
            if seq_info:
                logger.debug(f"Found {file_type} in {subfolder_name} (at {path.name}): {seq_info['frame_count']} frames")
                
                # Extract format
                if seq_info['files']:
                    format_ext = seq_info['files'][0].suffix[1:]  # .exr -> exr
                    try:
                        mtime = seq_info['files'][-1].stat().st_mtime
                    except OSError:
                        mtime = 0.0
                else:
                    format_ext = 'unknown'
                    mtime = 0.0
                
                return {
                    'subfolder': subfolder_name,
                    'pattern': seq_info['pattern'],
                    'frame_range': (seq_info['first_frame'], seq_info['last_frame']),
                    'frame_count': seq_info['frame_count'],
                    'format': format_ext,
                    'first_file': get_first_frame_path(seq_info),
                    'full_path': path, # Store the actual path where sequence was found
                    'mtime': mtime
                }

            # Fallback for single-file renders (mov/mp4/etc.) when no numbered sequence exists.
            video_matches = []
            for ext_pattern in extensions:
                if ext_pattern.lower() in self.VIDEO_PATTERNS:
                    video_matches.extend(path.glob(ext_pattern))

            if video_matches:
                best_video = max(video_matches, key=lambda p: p.stat().st_mtime)
                return {
                    'subfolder': subfolder_name,
                    'pattern': best_video.name,
                    'frame_range': None,
                    'frame_count': 0,
                    'format': best_video.suffix[1:].lower(),
                    'first_file': best_video,
                    'full_path': path,
                    'mtime': best_video.stat().st_mtime
                }
            return None
        except Exception as e:
            logger.error(f"Error checking path {path}: {e}")
            return None
    
    def _get_full_path(self, shot_folder: Path, file_info: dict) -> Path:
        """Get full path to sequence pattern, respecting recursion results."""
        if not file_info:
            return None
            
        # Use the specific directory found during deep search
        if 'full_path' in file_info:
             return file_info['full_path'] / file_info['pattern']

        # Fallback for shallow matches
        subfolder = shot_folder / file_info['subfolder']
        pattern = file_info['pattern']
        return subfolder / pattern
    
    def get_detection_stats(self) -> dict:
        """Get statistics about detection"""
        return {
            'total_shots': self.shots_found,
            'missing_scan': self.shots_missing_scan,
            'missing_render': self.shots_missing_render,
            'complete_shots': self.shots_found - max(self.shots_missing_scan, self.shots_missing_render)
        }

    def check_for_new_version(self, shot: ReviewShot) -> Optional[dict]:
        """
        Smart Watchdog: Check if a newer version exists for this shot.
        
        Returns dict with 'scan_path' or 'render_path' if update found, else None.
        """
        updates = {}
        scan_update_patterns = ['*.exr', '*.dpx', '*.tif', '*.tiff', '*.png', '*.jpg', '*.jpeg']
        render_update_patterns = scan_update_patterns + self.VIDEO_PATTERNS
        
        # 1. Check Scan for newer version
        if shot.scan_path:
            try:
                current_path = shot.scan_path
                
                # Parse current version from path
                match = re.search(r'[vV](\d+)', str(current_path))
                if not match:
                    logger.debug(f"No version found in scan path: {current_path}")
                else:
                    try:
                        current_ver = int(match.group(1))
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Invalid version number in scan path {current_path}: {e}")
                        current_ver = None
                    
                    if current_ver is not None:
                        # Find version directory in path
                        version_dir = None
                        for parent in current_path.parents:
                            if re.match(r'[vV]\d+', parent.name):
                                version_dir = parent
                                break
                        
                        if version_dir:
                            category_dir = version_dir.parent
                            
                            # Scan for higher versions
                            for child in category_dir.iterdir():
                                if child.is_dir():
                                    v_match = re.search(r'[vV](\d+)', child.name)
                                    if v_match:
                                        try:
                                            v_num = int(v_match.group(1))
                                            if v_num > current_ver:
                                                # Found newer version! Verify it has files
                                                seq_info = self._check_path_for_sequence(
                                                    child, "update", scan_update_patterns, "scan"
                                                )
                                                if seq_info:
                                                    updates['scan_ver'] = v_num
                                                    updates['scan_path'] = seq_info['full_path'] / seq_info['pattern']
                                                    logger.info(f"Found newer scan version: v{current_ver} → v{v_num}")
                                                    break  # Take first higher version
                                        except (ValueError, IndexError) as e:
                                            logger.debug(f"Skipping invalid version folder {child.name}: {e}")
                                            continue
            except Exception as e:
                logger.error(f"Error checking scan version for {shot.name}: {e}")
        
        # 2. Check Render for newer version (same logic)
        if shot.render_path:
            try:
                current_path = shot.render_path
                
                # Parse current version from path
                match = re.search(r'[vV](\d+)', str(current_path))
                if not match:
                    logger.debug(f"No version found in render path: {current_path}")
                else:
                    try:
                        current_ver = int(match.group(1))
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Invalid version number in render path {current_path}: {e}")
                        current_ver = None
                    
                    if current_ver is not None:
                        # Find version directory in path
                        version_dir = None
                        for parent in current_path.parents:
                            if re.match(r'[vV]\d+', parent.name):
                                version_dir = parent
                                break
                        
                        if version_dir:
                            category_dir = version_dir.parent
                            
                            # Scan for higher versions
                            for child in category_dir.iterdir():
                                if child.is_dir():
                                    v_match = re.search(r'[vV](\d+)', child.name)
                                    if v_match:
                                        try:
                                            v_num = int(v_match.group(1))
                                            if v_num > current_ver:
                                                # Found newer version! Verify it has files
                                                seq_info = self._check_path_for_sequence(
                                                    child, "update", render_update_patterns, "render"
                                                )
                                                if seq_info:
                                                    updates['render_ver'] = v_num
                                                    updates['render_path'] = seq_info['full_path'] / seq_info['pattern']
                                                    logger.info(f"Found newer render version: v{current_ver} → v{v_num}")
                                                    break  # Take first higher version
                                        except (ValueError, IndexError) as e:
                                            logger.debug(f"Skipping invalid version folder {child.name}: {e}")
                                            continue
            except Exception as e:
                logger.error(f"Error checking render version for {shot.name}: {e}")

        return updates if updates else None
