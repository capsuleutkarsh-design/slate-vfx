"""
SECURE Sequence Intelligence Utility.
Handles detection of image sequences, frame ranges, and missing frames (gaps).
"""
import re
from pathlib import Path
from typing import List, Dict, Tuple

class Sequence:
    def __init__(self, name: str, head: str, tail: str, frames: List[int], extension: str, path: Path):
        self.name = name          # e.g. "shot01_bg_v01"
        self.head = head          # e.g. "shot01_bg_v01."
        self.tail = tail          # e.g. ".exr"
        self.frames = sorted(frames)
        self.extension = extension
        self.path = path          # Parent path
        
    @property
    def range_str(self) -> str:
        """Returns range string: '1001-1050'"""
        if not self.frames: return "Empty"
        return f"{self.frames[0]}-{self.frames[-1]}"

    @property
    def missing_frames(self) -> List[int]:
        """Detect gaps in the sequence."""
        if len(self.frames) < 2: return []
        
        full_range = set(range(self.frames[0], self.frames[-1] + 1))
        existing = set(self.frames)
        missing = sorted(list(full_range - existing))
        return missing

    def __repr__(self):
        return f"<Sequence {self.name} [{self.range_str}] {self.extension}>"

class SequenceDetector:
    """Detects sequences in a directory."""
    
    # Regex for standard frame patterns: name.1001.ext, name_1001.ext
    # Captures: 1=BaseName, 2=Separator, 3=FrameNum, 4=Extension
    FRAME_REGEX = re.compile(r'^(.*?)(\.|_|-)(\d+)(\.[a-zA-Z0-9]+)$')

    @staticmethod
    def scan_directory(directory: Path) -> Tuple[List[Sequence], List[Path]]:
        """
        Scans a directory and returns (Sequences, SingleFiles).
        """
        if not directory.exists(): return [], []

        # The shared rules (slate.utils.sequence_utils.group_frames): the
        # same name and extension, consistent padding, and at least two
        # frames. One numbered file on its own used to count as a sequence.
        from slate.utils.sequence_utils import group_frames
        files = [item for item in directory.iterdir()
                 if item.is_file() and not item.name.startswith('.')]
        found, single_files = group_frames(files)

        result_seqs = []
        for fs in found:
            result_seqs.append(Sequence(
                fs.head.rstrip('._-') or fs.head,
                fs.head,
                fs.tail,
                fs.frames,
                fs.tail,  # Extension is tail
                directory,
            ))

        return result_seqs, single_files