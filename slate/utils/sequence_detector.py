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
    
    # Get all matching files
    files = sorted(folder.glob(pattern))
    if not files:
        return None
    
    # Extract frame numbers
    frame_data = []
    # Match frame number at end of stem, allowing common separators:
    # shot.1001.exr, shot_1001.exr, shot-1001.exr, shot1001.exr
    frame_pattern = re.compile(r'(?:[._-]?)(\d+)\.[^.]+$')
    
    # Do not treat single video files as sequences, even if they have digits
    video_extensions = {'.mov', '.mp4', '.mkv', '.avi', '.mxf', '.webm'}
    
    for f in files:
        if f.suffix.lower() in video_extensions:
            continue
            
        # Try to find frame number in filename
        match = frame_pattern.search(f.name)
        if match:
            frame_num = int(match.group(1))
            frame_data.append((frame_num, f))
    
    if not frame_data:
        # No frame numbers found, might be single file
        return None
    
    # Sort by frame number
    frame_data.sort(key=lambda x: x[0])
    
    frames = [f[0] for f in frame_data]
    files_sorted = [f[1] for f in frame_data]
    
    # Get matches for padding calculation
    # We find the specific string length of the first frame number in the filename
    first_file = files_sorted[0]
    match = frame_pattern.search(first_file.name)
    if not match:
        return None  # Should not happen since we already matched
        
    first_frame_str = match.group(1)
    padding = len(first_frame_str)
    
    # Generate frame pattern
    pattern_str = get_frame_pattern(first_file, padding, match)
    
    return {
        'pattern': pattern_str,
        'first_frame': min(frames),
        'last_frame': max(frames),
        'frame_count': len(frames),
        'padding': padding,
        'files': files_sorted,
        'missing_frames': find_missing_frames(frames)
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
