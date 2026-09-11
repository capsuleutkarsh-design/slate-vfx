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
        sequences: Dict[str, Dict] = {}
        single_files: List[Path] = []
        
        if not directory.exists(): return [], []

        for item in directory.iterdir():
            if not item.is_file(): continue
            if item.name.startswith('.'): continue # Skip hidden
            
            match = SequenceDetector.FRAME_REGEX.match(item.name)
            if match:
                base, sep, frame_str, ext = match.groups()
                # Create a unique key for grouping: "shotname_v01" + ".exr"
                # We include separator to distinguish shot.1001 vs shot_1001 if needed
                key = f"{base}{sep}#{ext}" 
                
                if key not in sequences:
                    sequences[key] = {
                        "name": base,
                        "head": f"{base}{sep}",
                        "tail": ext,
                        "frames": [],
                        "path": directory
                    }
                sequences[key]["frames"].append(int(frame_str))
            else:
                single_files.append(item)

        # Convert dict to Sequence objects
        result_seqs = []
        for key, data in sequences.items():
            # If only 1 frame, is it a sequence? Usually yes but could be ambiguous.
            # For now treat even 1 frame as sequence if it matched the regex number pattern.
            seq = Sequence(
                data["name"], 
                data["head"], 
                data["tail"], 
                data["frames"], 
                data["tail"], # Extension is tail
                data["path"]
            )
            result_seqs.append(seq)
            
        return result_seqs, single_files