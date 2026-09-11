"""
VFX Frame Sequence Utilities
使用 fileseq 进行帧序列处理 (行业标准)

This module provides utilities for detecting and managing frame sequences
using the industry-standard fileseq library (VFX Reference Platform).

Features:
- Automatic sequence detection from single frame
- Frame range extraction
- Printf-style pattern generation
- Directory scanning for all sequences
- Graceful fallback if fileseq not available
"""

from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
import logging
import re

try:
    import fileseq
    HAS_FILESEQ = True
except ImportError:
    HAS_FILESEQ = False
    logging.warning("fileseq not available - frame sequence detection disabled. Install with: pip install fileseq")


class SequenceDetector:
    """
    Frame sequence detection using VFX industry standard (fileseq).
    
    Usage:
        # Detect sequence from single frame
        seq = SequenceDetector.find_sequence(Path("/path/to/shot.1001.exr"))
        if seq:
            pattern = SequenceDetector.get_pattern(seq)  # "/path/to/shot.%04d.exr"
            start, end = SequenceDetector.get_frame_range(seq)  # (1001, 1100)
    """
    
    @staticmethod
    def is_available() -> bool:
        """Check if fileseq is available."""
        return HAS_FILESEQ

    @staticmethod
    def extract_frame_number(filename: str) -> Optional[int]:
        """
        Extract trailing frame number from a filename stem.

        Examples:
            shot.1001.exr -> 1001
            plate_A-0100.dpx -> 100
            image0001.png -> 1
        """
        stem = Path(str(filename)).stem
        match = re.search(r'(\d+)$', stem)
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def extract_base_name(filename: str) -> str:
        """
        Extract normalized basename (without trailing frame digits).

        Examples:
            shot_main-v001.1002.exr -> shot_main-v001
            plate_A-0100.dpx -> plate_a
            image0001.png -> image
        """
        stem = Path(str(filename)).stem
        base = re.sub(r'(?:[._-]?)(\d+)$', '', stem)
        return base.lower().rstrip('._-')
    
    @staticmethod
    def find_sequence(path: Path) -> Optional['fileseq.FileSequence']:
        """
        Detect frame sequence for a given file.
        
        Args:
            path: Path to any frame in the sequence
            
        Returns:
            FileSequence object or None if not a sequence or fileseq unavailable
            
        Example:
            >>> path = Path("/render/shot.1001.exr")
            >>> seq = SequenceDetector.find_sequence(path)
            >>> if seq:
            ...     logging.info(f"Found sequence: {seq}")
        """
        if not HAS_FILESEQ:
            logging.debug("fileseq not available, cannot detect sequences")
            return None
        
        if not path.exists():
            logging.warning(f"Path does not exist: {path}")
            return None
            
        try:
            # Find all sequences in the directory
            sequences = fileseq.findSequencesOnDisk(str(path.parent))
            frame_num = SequenceDetector.extract_frame_number(path.name)
            probe_base = SequenceDetector.extract_base_name(path.name)
            suffix_lower = path.suffix.lower()

            # Fast matching: compare extension + basename hint + frame range.
            for seq in sequences:
                try:
                    if str(seq.extension()).lower() != suffix_lower:
                        continue

                    seq_base = str(seq.basename() or "").lower().rstrip('._-')
                    if probe_base and seq_base:
                        if not (seq_base == probe_base or seq_base in probe_base or probe_base in seq_base):
                            continue

                    if frame_num is None:
                        return seq

                    start, end = seq.start(), seq.end()
                    if not (start <= frame_num <= end):
                        continue
                    return seq
                except Exception as e:
                    logging.debug(f"Error checking sequence {seq}: {e}")
                    continue
                    
        except Exception as e:
            logging.exception(f"Fileseq detection error for {path}: {e}")
        
        return None
    
    @staticmethod
    def get_frame_range(sequence: 'fileseq.FileSequence') -> Tuple[int, int]:
        """
        Get start and end frames of a sequence.
        
        Args:
            sequence: FileSequence object
            
        Returns:
            Tuple of (start_frame, end_frame)
            
        Example:
            >>> start, end = SequenceDetector.get_frame_range(seq)
            >>> logging.info(f"Frames: {start}-{end}")  # "Frames: 1001-1100"
        """
        return sequence.start(), sequence.end()
    
    @staticmethod
    def get_pattern(sequence: 'fileseq.FileSequence') -> str:
        """
        Get printf-style pattern for rendering.
        
        Args:
            sequence: FileSequence object
            
        Returns:
            Printf-style path pattern (e.g., "/path/to/shot.%04d.exr")
            
        Example:
            >>> pattern = SequenceDetector.get_pattern(seq)
            >>> logging.info(pattern)  # "/path/to/shot.%04d.exr"
        """
        parent = Path(sequence.dirname())
        pad_str = sequence.padding()
        
        # Convert fileseq padding notation to printf
        if pad_str == '#':
            pad_len = 4  # Default padding
        else:
            # Count padding characters (@, @@, etc.)
            pad_len = len(pad_str)
        
        printf_pad = f"%0{pad_len}d"
        pattern = str(parent / f"{sequence.basename()}{printf_pad}{sequence.extension()}")
        return pattern
    
    @staticmethod
    def get_frame_count(sequence: 'fileseq.FileSequence') -> int:
        """
        Get total number of frames in sequence.
        
        Args:
            sequence: FileSequence object
            
        Returns:
            Number of frames
        """
        return len(sequence)
    
    @staticmethod
    def get_missing_frames(sequence: 'fileseq.FileSequence') -> List[int]:
        """
        Detect missing frames in a sequence.
        
        Args:
            sequence: FileSequence object
            
        Returns:
            List of missing frame numbers
            
        Example:
            >>> missing = SequenceDetector.get_missing_frames(seq)
            >>> if missing:
            ...     logging.info(f"Missing frames: {missing}")
        """
        try:
            frame_set = sequence.frameSet()
            if frame_set is None:
                # fileseq reports a standalone file as a one-frame sequence with
                # no frame set. A single file cannot have gaps.
                return []

            start, end = sequence.start(), sequence.end()
            if start is None or end is None:
                return []

            # Get all frames that should exist
            expected_frames = set(range(start, end + 1))

            # Get frames that actually exist
            actual_frames = set(frame_set)
            
            # Return missing frames
            missing = sorted(expected_frames - actual_frames)
            return missing
            
        except Exception as e:
            logging.exception(f"Error detecting missing frames: {e}")
            return []
    
    @staticmethod
    def find_all_sequences(directory: Path) -> List['fileseq.FileSequence']:
        """
        Find all frame sequences in a directory.
        
        Args:
            directory: Directory path to scan
            
        Returns:
            List of FileSequence objects
            
        Example:
            >>> sequences = SequenceDetector.find_all_sequences(Path("/render"))
            >>> for seq in sequences:
            ...     logging.info(f"Found: {seq}")
        """
        if not HAS_FILESEQ:
            logging.debug("fileseq not available")
            return []
        
        if not directory.exists():
            logging.warning(f"Directory does not exist: {directory}")
            return []
        
        try:
            sequences = fileseq.findSequencesOnDisk(str(directory))
            return sequences
        except Exception as e:
            logging.exception(f"Fileseq directory scan error for {directory}: {e}")
            return []
    
    @staticmethod
    def get_sequence_info(sequence: 'fileseq.FileSequence') -> Dict[str, Any]:
        """
        Get comprehensive information about a sequence.
        
        Args:
            sequence: FileSequence object
            
        Returns:
            Dictionary with sequence metadata
            
        Example:
            >>> info = SequenceDetector.get_sequence_info(seq)
            >>> logging.info(f"Pattern: {info['pattern']}")
            >>> logging.info(f"Frames: {info['frame_range']}")
        """
        try:
            start, end = SequenceDetector.get_frame_range(sequence)
            pattern = SequenceDetector.get_pattern(sequence)
            frame_count = SequenceDetector.get_frame_count(sequence)
            missing = SequenceDetector.get_missing_frames(sequence)
            files = [Path(str(frame_path)) for frame_path in sequence]
            
            return {
                # New-style keys
                'pattern': pattern,
                'start_frame': start,
                'end_frame': end,
                'frame_count': frame_count,
                'frame_range': f"{start}-{end}",
                'missing_frames': missing,
                'has_missing': len(missing) > 0,
                'directory': sequence.dirname(),
                'basename': sequence.basename(),
                'extension': sequence.extension(),
                'padding': sequence.padding(),
                # Compatibility keys for legacy callers
                'first_frame': start,
                'last_frame': end,
                'files': files
            }
        except Exception as e:
            logging.exception(f"Error getting sequence info: {e}")
            return {}


class SequenceFallback:
    """
    Manual sequence detection fallback when fileseq is not available.
    
    This provides basic functionality for sequence detection using regex
    and file system scanning. Not as robust as fileseq but works in a pinch.
    """
    
    @staticmethod
    def detect_sequence_pattern(path: Path) -> Optional[Dict[str, Any]]:
        """
        Manually detect if a file is part of a sequence.
        
        Returns basic pattern info or None if not a sequence.
        """
        if not path.exists():
            return None
        
        stem = path.stem
        # Look for trailing digits (e.g., "shot.1001" -> "1001")
        match = re.search(r'(\d+)$', stem)
        
        if not match:
            return None  # No frame number
        
        frame_str = match.group(1)
        padding = len(frame_str)
        
        # Extract base name (before frame number)
        base = stem[:-len(frame_str)].rstrip('._-')
        ext = path.suffix
        
        # Try to find other frames
        parent = path.parent
        pattern = f"{base}.%0{padding}d{ext}"
        
        frames = []
        for file in parent.iterdir():
            if file.stem.startswith(base):
                match = re.search(r'(\d+)$', file.stem)
                if match and len(match.group(1)) == padding:
                    frames.append(int(match.group(1)))
        
        if len(frames) > 1:  # Must have at least 2 frames to be a sequence
            frames.sort()
            return {
                'pattern': str(parent / pattern),
                'start_frame': frames[0],
                'end_frame': frames[-1],
                'frame_count': len(frames),
                'padding': padding
            }
        
        return None


# Convenience function
def detect_sequence(path: Path) -> Optional[Dict[str, Any]]:
    """
    Detect sequence using fileseq if available, fallback to manual detection.
    
    Args:
        path: Path to check
        
    Returns:
        Sequence info dict or None
    """
    if HAS_FILESEQ:
        seq = SequenceDetector.find_sequence(path)
        if seq:
            return SequenceDetector.get_sequence_info(seq)
    
    # Fallback to manual detection
    return SequenceFallback.detect_sequence_pattern(path)


def get_sequence_info(folder: Path, patterns) -> Optional[Dict[str, Any]]:
    """
    Compatibility adapter for legacy callers that pass (folder, patterns).
    Uses fileseq-backed detection first, then fallback regex detection.
    """
    folder = Path(folder)
    if not folder.exists():
        return None

    if isinstance(patterns, str):
        patterns = [patterns]

    if HAS_FILESEQ:
        try:
            for candidate in sorted(folder.iterdir()):
                if not candidate.is_file():
                    continue
                if patterns and not any(candidate.match(pat) for pat in patterns):
                    continue
                seq = SequenceDetector.find_sequence(candidate)
                if seq:
                    return SequenceDetector.get_sequence_info(seq)
        except Exception as e:
            logging.debug(f"Fileseq compatibility detection failed in {folder}: {e}")

    for pattern in patterns or ["*"]:
        for candidate in sorted(folder.glob(pattern)):
            if not candidate.is_file():
                continue
            info = SequenceFallback.detect_sequence_pattern(candidate)
            if not info:
                continue
            # Keep legacy key names from fallback path.
            info.setdefault("first_frame", info.get("start_frame"))
            info.setdefault("last_frame", info.get("end_frame"))
            info.setdefault("files", [])
            return info
    return None


def get_first_frame_path(seq_info: dict) -> Optional[Path]:
    """Compatibility helper: return first frame path when available."""
    files = (seq_info or {}).get("files") or []
    if files:
        first = files[0]
        return first if isinstance(first, Path) else Path(first)
    return None


def format_pattern_with_frame(pattern: str, frame: int) -> str:
    """Compatibility helper: convert `%0Nd`/`%d` pattern to concrete filename."""
    pattern = str(pattern or "")
    match = re.search(r'%0(\d+)d', pattern)
    if match:
        padding = int(match.group(1))
        return pattern.replace(f'%0{padding}d', str(frame).zfill(padding))
    return pattern.replace('%d', str(frame))
