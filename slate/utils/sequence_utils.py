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
            
        # A file with no frame number is a still, whatever else is in the
        # folder. This used to return the first sequence with the same
        # extension, so every reference photo next to a render was handed to
        # the sequence player and failed to play (MED-002).
        frame_num = SequenceDetector.extract_frame_number(path.name)
        if frame_num is None or path.suffix.lower() in MOVIE_EXTENSIONS:
            return None

        try:
            # Find all sequences in the directory
            sequences = fileseq.findSequencesOnDisk(str(path.parent))
            probe_base = SequenceDetector.extract_base_name(path.name)
            suffix_lower = path.suffix.lower()

            # The same rules as group_frames(): the name before the number
            # must match exactly (it matched as a substring, so 'plate' took
            # 'plate_ref'), and one frame on its own is not a sequence.
            for seq in sequences:
                try:
                    if str(seq.extension()).lower() != suffix_lower:
                        continue

                    seq_base = str(seq.basename() or "").lower().rstrip('._-')
                    if seq_base != probe_base:
                        continue

                    if len(seq) < MIN_SEQUENCE_FRAMES:
                        continue

                    frame_set = seq.frameSet()
                    if frame_set is None or frame_num not in set(frame_set):
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
        # The shared rules (see group_frames): exact name match, the file's
        # own separator kept in the pattern, at least two frames.
        seq = sequence_for(path)
        return seq.info() if seq else None


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


# ---------------------------------------------------------------------------
# The one way to tell a sequence from a still
#
# Four parsers used to answer this question differently (this module, the
# proxy manager's utils/sequence_detector.py, the ingest worker's grouping
# and shot_media). The loosest of them said any file with the right extension
# in a folder with a sequence in it was part of that sequence - so a stock
# library's reference photos, textures and HDRIs were sent to the sequence
# player and failed with "Playback Error ... produced no picture". The rules,
# once, for everybody:
#
#   * a frame number is the last run of digits before the extension
#     (shot.1001.exr, shot_1001.exr, shot1001.exr)
#   * frames belong together when everything before the number and the
#     extension match exactly (case-insensitively), and the numbers are
#     padded the same way
#   * it takes at least two frames to make a sequence - one numbered file on
#     its own is a still (IMG_2045.jpg is a photo, not frame 2045)
#   * movie files are never frames
# ---------------------------------------------------------------------------

_FRAME_RE = re.compile(r'^(?P<head>.*?)(?P<frame>\d+)(?P<tail>\.[^.\\/]+)$')
MOVIE_EXTENSIONS = frozenset({'.mov', '.mp4', '.mkv', '.avi', '.mxf', '.webm', '.m4v', '.wmv', '.mpg', '.mpeg'})
MIN_SEQUENCE_FRAMES = 2


def parse_frame(name: str) -> Optional[Tuple[str, str, str]]:
    """('shot.', '1001', '.exr') for 'shot.1001.exr'; None when there is no frame number."""
    match = _FRAME_RE.match(Path(str(name)).name)
    if not match:
        return None
    if match.group('tail').lower() in MOVIE_EXTENSIONS:
        return None
    return match.group('head'), match.group('frame'), match.group('tail')


def _padding_of(digits: str) -> int:
    """How wide the numbers are written: '0100' -> 4, '1001' -> 0 (cannot tell)."""
    return len(digits) if digits.startswith('0') and len(digits) > 1 else 0


def _fits(digits: str, padding: int) -> bool:
    """Whether a frame's digits are written the way a sequence padded to `padding` writes them."""
    if not padding:
        return not (digits.startswith('0') and len(digits) > 1)
    return len(digits) == padding or (len(digits) > padding and not digits.startswith('0'))


class FrameSequence:
    """Frames that belong together - see group_frames()."""

    def __init__(self, directory: Path, head: str, tail: str, padding: int,
                 frames: List[int], files: List[Path]):
        order = sorted(range(len(frames)), key=lambda i: frames[i])
        self.directory = Path(directory)
        self.head = head
        self.tail = tail
        self.padding = padding
        self.frames = [frames[i] for i in order]
        self.files = [files[i] for i in order]

    @property
    def start(self) -> int:
        return self.frames[0]

    @property
    def end(self) -> int:
        return self.frames[-1]

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def width(self) -> int:
        """Digits to print a frame with: the padding, or the widest number."""
        return self.padding or len(str(self.end))

    @property
    def filename_pattern(self) -> str:
        """'shot.%04d.exr' (or 'shot.%d.exr' for unpadded numbers)."""
        return f"{self.head}%0{self.padding}d{self.tail}" if self.padding else f"{self.head}%d{self.tail}"

    @property
    def pattern(self) -> str:
        """The printf pattern with its folder."""
        return str(self.directory / self.filename_pattern)

    @property
    def missing_frames(self) -> List[int]:
        present = set(self.frames)
        return [f for f in range(self.start, self.end + 1) if f not in present]

    def frame_path(self, frame: int) -> Path:
        digits = str(frame).zfill(self.padding) if self.padding else str(frame)
        return self.directory / f"{self.head}{digits}{self.tail}"

    def ffconcat(self, first: int, fps: float = 24.0) -> str:
        """
        An ffmpeg frame list from `first` to the end, a gap holding the frame before it.

        ffmpeg's image reader stops at the first missing frame (and will not
        start on one), so a render with a frame missing played, and was made
        into a proxy, only up to the gap. Read through this list it runs the
        whole range, as RV plays it.
        """
        present = set(self.frames)
        held = max((f for f in self.frames if f <= first), default=self.start)
        lines = ["ffconcat version 1.0"]
        for frame in range(first, self.end + 1):
            if frame in present:
                held = frame
            path = str(self.frame_path(held)).replace("'", "'\\''")
            lines += [f"file '{path}'", f"duration {1.0 / fps:.9f}"]
        return "\n".join(lines) + "\n"

    def info(self) -> Dict[str, Any]:
        """The same keys get_sequence_info() has always returned."""
        missing = self.missing_frames
        return {
            'pattern': self.pattern,
            'filename_pattern': self.filename_pattern,
            'start_frame': self.start,
            'end_frame': self.end,
            'frame_count': self.frame_count,
            'frame_range': f"{self.start}-{self.end}",
            'missing_frames': missing,
            'has_missing': bool(missing),
            'directory': str(self.directory),
            'basename': self.head,
            'extension': self.tail,
            'padding': self.padding or self.width,
            'first_frame': self.start,
            'last_frame': self.end,
            'files': list(self.files),
        }

    def __repr__(self):
        return f"<FrameSequence {self.filename_pattern} {self.start}-{self.end} ({self.frame_count})>"


def group_frames(paths, min_frames: int = MIN_SEQUENCE_FRAMES):
    """
    Split files into sequences and stills.

    Returns (sequences, stills): FrameSequence objects, largest first, and the
    Paths that are not part of any sequence - files with no frame number,
    movies, and numbered files that turned out to be alone.
    """
    groups: Dict[Tuple[str, str, str], List[Tuple[str, Path]]] = {}
    stills: List[Path] = []
    for raw in paths:
        path = Path(raw)
        parsed = parse_frame(path.name)
        if not parsed:
            stills.append(path)
            continue
        head, digits, tail = parsed
        key = (str(path.parent).lower(), head.lower(), tail.lower())
        groups.setdefault(key, []).append((digits, path))

    need = max(int(min_frames), 1)
    sequences: List[FrameSequence] = []
    for members in groups.values():
        # The padding is what the zero-padded names say ('0999' -> 4); a
        # frame past it ('1000') fits too. Names written another way
        # ('shot.5.exr' beside 'shot.0004.exr') are not part of it.
        pads = [_padding_of(d) for d, _ in members if _padding_of(d)]
        padding = max(pads) if pads else 0
        inside = [(d, p) for d, p in members if _fits(d, padding)]
        outside = [p for d, p in members if not _fits(d, padding)]
        frames = [int(d) for d, _ in inside]
        if len(set(frames)) >= need:
            first = inside[0][1]
            head, _digits, tail = parse_frame(first.name)
            sequences.append(FrameSequence(first.parent, head, tail, padding,
                                           frames, [p for _, p in inside]))
            stills.extend(outside)
        else:
            stills.extend(p for _, p in members)
    sequences.sort(key=lambda s: (-s.frame_count, s.filename_pattern.lower()))
    return sequences, stills


def sequence_for(path: Path, min_frames: int = MIN_SEQUENCE_FRAMES) -> Optional[FrameSequence]:
    """
    The sequence this file is a frame of, or None when it is a still.

    Only files that match it exactly are considered: same folder, same text
    before the number, same extension, same padding.
    """
    path = Path(path)
    parsed = parse_frame(path.name)
    if not parsed or not path.parent.exists():
        return None
    head, digits, tail = parsed
    try:
        siblings = [p for p in path.parent.iterdir()
                    if p.is_file() and p.name.lower().startswith(head.lower())
                    and p.suffix.lower() == tail.lower()]
    except OSError:
        return None
    sequences, _ = group_frames(siblings, min_frames=min_frames)
    wanted = path.name.lower()
    for seq in sequences:
        if any(p.name.lower() == wanted for p in seq.files):
            return seq
    return None
