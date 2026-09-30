"""
Image Sequence Detection Utility

Detects and analyzes image sequences in directories.
Extracts frame ranges, patterns, and metadata.
"""

import re
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def detect_sequence(folder: Path, pattern: str = "*") -> Optional[dict]:
    """
    Detect image sequence in folder matching pattern
    
    Args:
        folder: Directory to search
        pattern: Glob pattern (e.g., '*.exr', 'Shot_010.*')
    
    Returns:
        Dict with sequence info or None if no sequence found
        {
            'pattern': 'Shot_010.%04d.exr',
            'first_frame': 1001,
            'last_frame': 1120,
            'frame_count': 120,
            'padding': 4,
            'files': [Path, Path, ...]
        }
    """
    if not folder.exists():
        return None

    files = sorted(p for p in folder.glob(pattern) if p.is_file())
    if not files:
        return None

    # The shared rules (slate.utils.sequence_utils.group_frames). This used to
    # take every numbered file the glob matched as one sequence - two
    # sequences whose names shared a prefix were mixed together, and a
    # single numbered still counted as a one-frame sequence.
    from slate.utils.sequence_utils import group_frames
    sequences, _stills = group_frames(files)
    if not sequences:
        return None
    seq = sequences[0]
    return {
        'pattern': seq.filename_pattern,
        'first_frame': seq.start,
        'last_frame': seq.end,
        'frame_count': seq.frame_count,
        'padding': seq.padding or seq.width,
        'files': list(seq.files),
        'missing_frames': seq.missing_frames,
    }


def get_frame_pattern(file_path: Path, padding: int, match: re.Match) -> str:
    """
    Convert file path to frame pattern using regex match to be safe.
    """
    # reconstruct pattern by replacing the EXACT matched digit span
    start, end = match.span(1) # Span of the digits
    original_name = file_path.name
    
    # Replace ONLY the digits at that specific position
    pattern = original_name[:start] + f'%0{padding}d' + original_name[end:]
    return pattern


def find_missing_frames(frames: list[int]) -> list[int]:
    """Find missing frames in sequence"""
    if not frames:
        return []
    
    first = min(frames)
    last = max(frames)
    expected = set(range(first, last + 1))
    actual = set(frames)
    
    return sorted(expected - actual)


def get_sequence_info(folder: Path, patterns) -> Optional[dict]:
    """
    Try multiple patterns to find sequence
    
    Args:
        folder: Directory to search
        patterns: List of glob patterns to try
    
    Returns:
        First matching sequence info or None
    """
    # Backward compatibility: accept a single pattern string.
    if isinstance(patterns, str):
        patterns = [patterns]

    for pattern in patterns:
        seq_info = detect_sequence(folder, pattern)
        if seq_info:
            return seq_info
    
    return None


def validate_sequence(folder: Path, pattern: str, min_frames: int = 1) -> bool:
    """
    Validate that a valid sequence exists
    
    Args:
        folder: Directory to check
        pattern: Glob pattern
        min_frames: Minimum number of frames required
    
    Returns:
        True if valid sequence found
    """
    seq_info = detect_sequence(folder, pattern)
    if not seq_info:
        return False
    
    return seq_info['frame_count'] >= min_frames


def get_first_frame_path(seq_info: dict) -> Optional[Path]:
    """Get path to first frame in sequence"""
    if seq_info and seq_info.get('files'):
        return seq_info['files'][0]
    return None


def format_pattern_with_frame(pattern: str, frame: int) -> str:
    """
    Convert pattern to specific frame filename
    
    Example:
        ('Shot_010.%04d.exr', 1001) -> 'Shot_010.1001.exr'
    """
    # Extract padding from pattern
    match = re.search(r'%0(\d+)d', pattern)
    if match:
        padding = int(match.group(1))
        frame_str = str(frame).zfill(padding)
        return pattern.replace(f'%0{padding}d', frame_str)
    
    # Fallback
    return pattern.replace('%d', str(frame))
