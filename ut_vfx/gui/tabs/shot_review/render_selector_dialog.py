"""
Render Selector Dialog — Smart multi-type render detection and selection.

Extracted from ShotReviewTab.choose_manual_render (was 225 lines).

Usage:
    dialog = RenderSelectorDialog(shot, project_path, parent)
    result = dialog.exec()
    if result and dialog.selected_render:
        render_path, sequence_info, frame_range = dialog.selected_render
"""

from pathlib import Path
import logging

from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox, QInputDialog,
)

from ....utils.sequence_utils import get_sequence_info

logger = logging.getLogger(__name__)

# Common render type folder names
RENDER_TYPES = ['PREP', 'COMP', 'SLAPCOMP', 'PRECOMP', 'OUTPUT', 'FINAL', 'BG', 'FG']
SEQUENCE_PATTERNS = ['*.exr', '*.dpx', '*.jpg', '*.jpeg', '*.png', '*.tif', '*.tiff']
VIDEO_PATTERNS = ['*.mov', '*.mp4', '*.mkv', '*.avi', '*.mxf']


class RenderSelectorDialog:
    """
    Scans a user-chosen folder for render types (PREP, COMP, etc.),
    detects image sequences and video files, and lets the user pick one.

    After exec(), check `self.selected_render` for the result:
        (render_path: Path, sequence_info: dict, is_video: bool)
    or None if cancelled.
    """

    def __init__(self, shot, project_path=None, parent=None):
        self.shot = shot
        self.project_path = project_path
        self.parent = parent
        self.selected_render = None  # (render_path, seq_info, is_video)

    def exec(self) -> bool:
        """Run the folder selection + render detection flow.

        Returns True if user selected a render, False if cancelled.
        """
        # Determine start directory
        start_dir = str(Path.home())
        if self.shot.scan_path:
            start_dir = str(self.shot.scan_path.parent.parent)
        elif self.project_path:
            start_dir = str(self.project_path)

        # Step 1: Let user choose folder
        folder = QFileDialog.getExistingDirectory(
            self.parent,
            f"Select Render Root Folder for {self.shot.name}",
            start_dir,
            options=QFileDialog.Option.DontUseNativeDialog | QFileDialog.Option.ShowDirsOnly
        )

        if not folder:
            return False

        folder_path = Path(folder)

        # Step 2: Scan for render options
        render_options = self._scan_for_renders(folder_path)

        if not render_options:
            QMessageBox.warning(
                self.parent,
                "No Renders Found",
                f"Could not find any render sequences in:\n{folder}\n\n"
                "Searched for: PREP, COMP, SLAPCOMP, PRECOMP, etc.\n"
                "Supported formats: EXR, DPX, JPG, PNG, TIF, MOV, MP4"
            )
            return False

        # Step 3: Let user select if multiple options
        if len(render_options) == 1:
            selected_display, selected_path, found_sequence = render_options[0]
        else:
            display_items = [opt[0] for opt in render_options]
            item, ok = QInputDialog.getItem(
                self.parent,
                "Select Render Type",
                f"Found {len(render_options)} render options. Choose one:",
                display_items, 0, False
            )
            if not ok or not item:
                return False
            index = display_items.index(item)
            selected_display, selected_path, found_sequence = render_options[index]

        # Step 4: Build result
        render_path = selected_path / found_sequence['pattern']
        is_video = bool(found_sequence.get('is_video'))

        self.selected_render = (render_path, found_sequence, is_video)
        self.selected_folder = folder
        return True

    def _scan_for_renders(self, folder_path: Path) -> list:
        """Scan folder for render options (sequences + videos)."""
        render_options = []

        # 1. Check current folder first
        self._scan_folder_for_sequences(folder_path, "CURRENT FOLDER", render_options)
        if not render_options:
            self._add_video_option(folder_path, "CURRENT FOLDER", render_options)

        # 2. Scan subfolders for render types
        try:
            subfolders = [f for f in folder_path.iterdir() if f.is_dir()]
        except OSError:
            subfolders = []

        for subfolder in subfolders:
            folder_name_upper = subfolder.name.upper()
            matched_type = None
            for render_type in RENDER_TYPES:
                if render_type in folder_name_upper:
                    matched_type = render_type
                    break

            if matched_type:
                exr_subfolder = subfolder / 'EXR'
                dpx_subfolder = subfolder / 'DPX'

                found = False
                for check_folder in [exr_subfolder, dpx_subfolder, subfolder]:
                    if not check_folder.exists():
                        continue
                    if self._scan_folder_for_sequences(check_folder, matched_type, render_options):
                        found = True
                        break
                if not found:
                    for check_folder in [exr_subfolder, dpx_subfolder, subfolder]:
                        if not check_folder.exists():
                            continue
                        if self._add_video_option(check_folder, matched_type, render_options):
                            break

        return render_options

    def _scan_folder_for_sequences(self, folder: Path, label: str, render_options: list) -> bool:
        """Scan single folder for image sequences. Returns True if found."""
        for pattern in SEQUENCE_PATTERNS:
            try:
                seq_info = get_sequence_info(folder, [pattern])
                if seq_info:
                    display = f"[{label}] {seq_info['pattern']} ({seq_info['first_frame']}-{seq_info['last_frame']})"
                    render_options.append((display, folder, seq_info))
                    return True
            except (OSError, ValueError, KeyError) as e:
                logger.debug(f"Failed to detect sequence with pattern {pattern}: {e}")
                continue
        return False

    @staticmethod
    def _add_video_option(search_folder: Path, label: str, render_options: list) -> bool:
        """Add latest video render candidate from folder, if present."""
        for vp in VIDEO_PATTERNS:
            files = list(search_folder.glob(vp))
            if files:
                latest = max(files, key=lambda p: p.stat().st_mtime)
                render_options.append((
                    f"[{label}] {latest.name} (video)",
                    search_folder,
                    {
                        'pattern': latest.name,
                        'first_frame': None,
                        'last_frame': None,
                        'is_video': True
                    }
                ))
                return True
        return False


def apply_render_selection(shot, render_path, found_sequence, is_video):
    """Apply the selected render to a ReviewShot, computing frame ranges.

    Returns:
        dict with 'frame_range' and 'format' that was applied.
    """
    shot.render_path = render_path

    if is_video:
        if shot.frame_range:
            shot.render_start = shot.frame_range[0]
            shot.render_end = shot.frame_range[1]
        else:
            shot.render_start = 1
            shot.render_end = 100
    else:
        shot.render_start = found_sequence['first_frame']
        shot.render_end = found_sequence['last_frame']

    # SMART FRAME RANGE: Handle mismatched scan/render ranges
    if not is_video and hasattr(shot, 'scan_start') and hasattr(shot, 'scan_end'):
        scan_start = shot.scan_start
        scan_end = shot.scan_end
        render_start = shot.render_start
        render_end = shot.render_end

        common_start = max(scan_start, render_start)
        common_end = min(scan_end, render_end)

        if common_start <= common_end:
            shot.frame_range = (common_start, common_end)
            logger.info(f"Frame range adjusted to common frames: {common_start}-{common_end}")
        else:
            shot.frame_range = (render_start, render_end)
            logger.warning(f"No frame overlap! Scan: {scan_start}-{scan_end}, Render: {render_start}-{render_end}")
    else:
        if not is_video:
            shot.frame_range = (found_sequence['first_frame'], found_sequence['last_frame'])
        elif not shot.frame_range:
            shot.frame_range = (shot.render_start, shot.render_end)

    # Detect format from extension
    ext = found_sequence['pattern'].lower()
    if '.exr' in ext:
        shot.format = 'EXR'
    elif '.dpx' in ext:
        shot.format = 'DPX'
    elif '.jpg' in ext or '.jpeg' in ext:
        shot.format = 'JPG'
    elif '.png' in ext:
        shot.format = 'PNG'
    elif ext.endswith('.mov') or ext.endswith('.mp4') or ext.endswith('.mkv') or ext.endswith('.avi') or ext.endswith('.mxf'):
        shot.format = 'VIDEO'

    # Clear hydration cache so it reflects the manual selection
    setattr(shot, "_media_hydrated", False)
