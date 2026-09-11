"""
VFX Review Shot Data Models

Defines data structures for shot review workflow including
shot metadata, review status, and lineup management.
"""

from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from enum import Enum
from typing import Optional


class ShotStatus(Enum):
    """Shot review status"""
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    RE_REVIEW = "re_review"


@dataclass
class ReviewShot:
    """Shot data for review workflow"""
    
    # Identity
    id: str
    name: str
    sequence: str
    project_name: str = ""
    
    # File paths (can be None if not found)
    scan_path: Optional[Path] = None
    render_path: Optional[Path] = None
    
    # Metadata
    frame_range: Optional[tuple[int, int]] = None
    resolution: Optional[tuple[int, int]] = None
    fps: float = 24.0
    format: str = "unknown"
    bit_depth: int = 8
    
    # Review data
    status: ShotStatus = ShotStatus.PENDING
    notes: list[str] = field(default_factory=list)
    reviewer: str = ""
    review_date: Optional[datetime] = None
    
    # Continuity issues
    continuity_issues: list[str] = field(default_factory=list)
    
    # Timeline/Lineup
    in_lineup: bool = False
    lineup_order: int = 0
    
    # Proxy management
    proxy_path: Optional[Path] = None
    scan_proxy_path: Optional[Path] = None
    render_proxy_path: Optional[Path] = None
    proxy_status: str = "none"  # none, generating, ready
    
    def __str__(self):
        return f"{self.name} [{self.status.value}]"
    
    def has_scan(self) -> bool:
        """Check if scan path exists"""
        if self.scan_path is None:
            return False
            
        try:
            # If path is a sequence pattern (e.g. shot.%04d.exr), checking .exists() fails
            # So we check if the parent directory exists instead
            if '%' in str(self.scan_path):
                return self.scan_path.parent.exists()
                
            return self.scan_path.exists()
        except OSError:
            return False
    
    def has_render(self) -> bool:
        """Check if render path exists"""
        if self.render_path is None:
            return False
            
        try:    
            if '%' in str(self.render_path):
                return self.render_path.parent.exists()
                
            return self.render_path.exists()
        except OSError:
            return False
    
    def is_complete(self) -> bool:
        """Check if both scan and render are found"""
        return self.has_scan() and self.has_render()
    
    def get_frame_count(self) -> int:
        """Get total frame count"""
        if self.frame_range:
            return self.frame_range[1] - self.frame_range[0] + 1
        return 0


@dataclass
class ReviewLineup:
    """Timeline/Lineup for approved shots"""
    
    name: str
    shots: list[ReviewShot] = field(default_factory=list)
    created_date: datetime = field(default_factory=datetime.now)
    export_settings: dict = field(default_factory=dict)
    
    def add_shot(self, shot: ReviewShot):
        """Add shot to lineup"""
        if shot not in self.shots:
            shot.in_lineup = True
            shot.lineup_order = len(self.shots)
            self.shots.append(shot)
    
    def remove_shot(self, shot: ReviewShot):
        """Remove shot from lineup"""
        if shot in self.shots:
            self.shots.remove(shot)
            shot.in_lineup = False
            # Reorder remaining shots
            for i, s in enumerate(self.shots):
                s.lineup_order = i
    
    def get_total_frames(self) -> int:
        """Get total frame count of lineup"""
        return sum(shot.get_frame_count() for shot in self.shots)
    
    def get_duration_seconds(self, fps: float = 24.0) -> float:
        """Get total duration in seconds"""
        return self.get_total_frames() / fps
