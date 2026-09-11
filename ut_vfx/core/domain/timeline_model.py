from dataclasses import dataclass, field
from typing import List, Optional
import uuid

@dataclass
class TimelineClip:
    """
    Represents a clip on a track.
    Refers to a 'shot' object but manages its own timing.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    shot: object = None  # Reference to ReviewShot
    track_type: str = "render" # 'scan' or 'render'
    start_frame: int = 0 # Absolute timeline start
    duration: int = 100
    source_in: int = 0   # Slip/Slide support
    
    @property
    def end_frame(self):
        return self.start_frame + self.duration

@dataclass
class TimelineTrack:
    """
    A single track containing non-overlapping clips.
    """
    id: str
    name: str
    track_type: str # 'video', 'audio' (future)
    clips: List[TimelineClip] = field(default_factory=list)
    
    def can_add_clip(self, new_clip: TimelineClip) -> bool:
        """Check for collisions"""
        for clip in self.clips:
            if clip.id == new_clip.id: continue # specific check if moving same clip
            
            # Check overlap
            # A Starts inside B or A Ends inside B or B is inside A
            if (new_clip.start_frame < clip.end_frame and new_clip.end_frame > clip.start_frame):
                return False
        return True
        
    def add_clip(self, clip: TimelineClip) -> bool:
        """Add clip if space available"""
        if self.can_add_clip(clip):
            self.clips.append(clip)
            self.clips.sort(key=lambda c: c.start_frame)
            return True
        return False
        
    def remove_clip(self, clip_id: str):
        self.clips = [c for c in self.clips if c.id != clip_id]
        
    def get_clip_at(self, frame: int) -> Optional[TimelineClip]:
        for clip in self.clips:
            if clip.start_frame <= frame < clip.end_frame:
                return clip
        return None

class TimelineModel:
    """
    The Brain of the NLE.
    Manages multiple tracks and ensures consistency.
    """
    def __init__(self):
        self.tracks: List[TimelineTrack] = []
        self.duration: int = 1000
        
        # Init standard tracks
        self.add_track("V3", "render")
        self.add_track("V2", "render")
        self.add_track("V1", "scan")
        
    def add_track(self, name, type="video"):
        t = TimelineTrack(id=str(uuid.uuid4()), name=name, track_type=type)
        self.tracks.append(t)
        return t
        
    def get_track_by_index(self, index):
        if 0 <= index < len(self.tracks):
            return self.tracks[index]
        return None
        
    def add_shot(self, shot, start_frame, duration):
        """
        High level helper: Add shot to standard tracks (V1/V2)
        Premiere Logic: Overwrite or Insert? 
        For Lineup, usually Overwrite/Placement is safest first.
        """
        # Create clips
        clip_render = TimelineClip(shot=shot, track_type="render", start_frame=start_frame, duration=duration)
        clip_scan = TimelineClip(shot=shot, track_type="scan", start_frame=start_frame, duration=duration)
        
        # Try add to V2 (Render) and V1 (Scan)
        # Assuming V2 is index 1, V1 is index 2 (based on list order V3, V2, V1)
        # Refine finding tracks by name or type later
        
        # Simple Logic: Find first available 'render' track and 'scan' track
        added_render = False
        added_scan = False
        
        for t in self.tracks:
            if not added_render and t.name.startswith("V2"): # Convention
                if t.add_clip(clip_render):
                    added_render = True
            
            if not added_scan and t.name.startswith("V1"):
                if t.add_clip(clip_scan):
                    added_scan = True
                    
        return added_render, added_scan
