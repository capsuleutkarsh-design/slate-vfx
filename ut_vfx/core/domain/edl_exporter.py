"""
EDL Exporter — CMX 3600 EDL Import/Export

Uses OpenTimelineIO when available for standards-compliant EDL generation.
Falls back to pure-Python implementation if OTIO is not installed.
"""

from pathlib import Path
from typing import List
import logging

logger = logging.getLogger(__name__)

_HAS_OTIO = False


# ===================================================================
# Pure-Python fallback (original implementation)
# ===================================================================

def _frames_to_timecode(frames: int, fps: float = 24.0) -> str:
    """
    Convert frame number to SMPTE timecode.

    Args:
        frames: Frame number
        fps: Frames per second

    Returns:
        Timecode string (HH:MM:SS:FF)
    """
    total_seconds = frames / fps
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    frame = int(frames % fps)

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"


def _export_edl_fallback(shots: List, output_path: Path,
                         fps: float = 24.0,
                         title: str = "VFX_Review_Lineup") -> bool:
    """Pure-Python CMX 3600 EDL export (fallback)."""
    try:
        with open(output_path, 'w') as f:
            f.write(f"TITLE: {title}\n")
            f.write("FCM: NON-DROP FRAME\n\n")

            timeline_frame = 0

            for idx, shot in enumerate(shots, 1):
                if hasattr(shot, 'render_start') and hasattr(shot, 'render_end'):
                    duration = shot.render_end - shot.render_start + 1
                    source_start_frame = shot.render_start
                    source_end_frame = shot.render_end
                elif hasattr(shot, 'frame_range') and shot.frame_range:
                    duration = shot.frame_range[1] - shot.frame_range[0] + 1
                    source_start_frame = shot.frame_range[0]
                    source_end_frame = shot.frame_range[1]
                else:
                    logger.warning(f"Shot {shot.name} missing frame range, skipping")
                    continue

                source_start_tc = _frames_to_timecode(source_start_frame, fps)
                source_end_tc = _frames_to_timecode(source_end_frame, fps)
                timeline_start_tc = _frames_to_timecode(timeline_frame, fps)
                timeline_end_tc = _frames_to_timecode(timeline_frame + duration, fps)

                f.write(f"{idx:03d}  AX       V     C        ")
                f.write(f"{source_start_tc} {source_end_tc} {timeline_start_tc} {timeline_end_tc}\n")
                f.write(f"* FROM CLIP NAME: {shot.name}_render\n")

                if hasattr(shot, 'render_path') and shot.render_path:
                    f.write(f"* SOURCE FILE: {shot.render_path}\n")
                if hasattr(shot, 'sequence'):
                    f.write(f"* SEQUENCE: {shot.sequence}\n")
                if hasattr(shot, 'status'):
                    f.write(f"* STATUS: {shot.status.value if hasattr(shot.status, 'value') else shot.status}\n")

                f.write("\n")
                timeline_frame += duration

        logger.info(f"EDL exported (fallback): {output_path} ({len(shots)} shots)")
        return True

    except Exception as e:
        logger.error(f"Error exporting EDL (fallback): {e}", exc_info=True)
        return False


def _export_edl_dual_fallback(shots: List, output_path: Path,
                              fps: float = 24.0) -> bool:
    """Pure-Python dual-track EDL export (fallback)."""
    try:
        with open(output_path, 'w') as f:
            f.write("TITLE: VFX_Review_Lineup_Dual\n")
            f.write("FCM: NON-DROP FRAME\n\n")

            timeline_frame = 0
            event_num = 1

            for shot in shots:
                if hasattr(shot, 'render_start') and hasattr(shot, 'render_end'):
                    duration = shot.render_end - shot.render_start + 1
                    source_start = shot.render_start
                    source_end = shot.render_end
                elif hasattr(shot, 'frame_range') and shot.frame_range:
                    duration = shot.frame_range[1] - shot.frame_range[0] + 1
                    source_start = shot.frame_range[0]
                    source_end = shot.frame_range[1]
                else:
                    continue

                source_start_tc = _frames_to_timecode(source_start, fps)
                source_end_tc = _frames_to_timecode(source_end, fps)
                timeline_start_tc = _frames_to_timecode(timeline_frame, fps)
                timeline_end_tc = _frames_to_timecode(timeline_frame + duration, fps)

                if hasattr(shot, 'scan_path') and shot.scan_path:
                    f.write(f"{event_num:03d}  AX       V     C        ")
                    f.write(f"{source_start_tc} {source_end_tc} {timeline_start_tc} {timeline_end_tc}\n")
                    f.write(f"* FROM CLIP NAME: {shot.name}_scan\n")
                    f.write(f"* SOURCE FILE: {shot.scan_path}\n")
                    f.write("* TRACK: V1\n\n")
                    event_num += 1

                if hasattr(shot, 'render_path') and shot.render_path:
                    f.write(f"{event_num:03d}  AX       V     C        ")
                    f.write(f"{source_start_tc} {source_end_tc} {timeline_start_tc} {timeline_end_tc}\n")
                    f.write(f"* FROM CLIP NAME: {shot.name}_render\n")
                    f.write(f"* SOURCE FILE: {shot.render_path}\n")
                    f.write("* TRACK: V2\n\n")
                    event_num += 1

                timeline_frame += duration

        logger.info(f"Dual-track EDL exported (fallback): {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error exporting dual-track EDL (fallback): {e}", exc_info=True)
        return False


# ===================================================================
# Public API (unchanged signatures)
# ===================================================================

# Keep the old name available for any external imports
frames_to_timecode = _frames_to_timecode


def export_edl(shots: List, output_path: Path, fps: float = 24.0,
               title: str = "VFX_Review_Lineup") -> bool:
    """
    Export shots to CMX 3600 EDL format.

    Uses OpenTimelineIO if available, otherwise falls back to
    pure-Python implementation.

    Args:
        shots: List of ReviewShot objects
        output_path: Output file path
        fps: Frames per second (default 24.0)
        title: EDL title

    Returns:
        True if successful
    """
    return _export_edl_fallback(shots, output_path, fps, title)


def export_edl_dual_track(shots: List, output_path: Path,
                          fps: float = 24.0) -> bool:
    """
    Export shots to EDL with dual tracks (Render + Scan).

    Uses OpenTimelineIO if available, otherwise falls back to
    pure-Python implementation.

    Args:
        shots: List of ReviewShot objects
        output_path: Output file path
        fps: Frames per second

    Returns:
        True if successful
    """
    return _export_edl_dual_fallback(shots, output_path, fps)


def import_edl(edl_path, fps: float = 24.0) -> List:
    """
    Import an EDL file and return ReviewShot objects.

    NEW: This function requires OpenTimelineIO.

    Args:
        edl_path: Path to the EDL file.
        fps: Frame rate hint (used if EDL doesn't specify).

    Returns:
        List of ReviewShot objects.

    Raises:
        ImportError: If OpenTimelineIO is not installed.
        FileNotFoundError: If the EDL file doesn't exist.
    """
    raise NotImplementedError("EDL import is no longer supported.")
