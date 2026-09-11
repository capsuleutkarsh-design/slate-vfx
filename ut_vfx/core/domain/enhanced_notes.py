"""
Enhanced Notes System

Time-stamped, categorized notes with priority levels.
Supports @mentions and attachments.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List
from enum import Enum


class NoteCategory(Enum):
    """Note categories"""
    GENERAL = "general"
    ROTO = "roto"
    COMP = "comp"
    COLOR = "color"
    FX = "fx"
    TRACKING = "tracking"
    PAINT = "paint"
    OTHER = "other"


class NotePriority(Enum):
    """Note priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class EnhancedNote:
    """Enhanced note with metadata"""
    
    text: str
    author: str
    timestamp: datetime = field(default_factory=datetime.now)
    category: NoteCategory = NoteCategory.GENERAL
    priority: NotePriority = NotePriority.MEDIUM
    mentions: List[str] = field(default_factory=list)
    attachments: List[Path] = field(default_factory=list)
    resolved: bool = False
    
    def __str__(self):
        """Format for display"""
        time_str = self.timestamp.strftime("%Y-%m-%d %H:%M")
        priority_icon = self._get_priority_icon()
        
        return f"{priority_icon} [{time_str}] {self.author}: {self.text}"
    
    def _get_priority_icon(self) -> str:
        """Get emoji for priority"""
        icons = {
            NotePriority.LOW: '🔵',
            NotePriority.MEDIUM: '🟡',
            NotePriority.HIGH: '🟠',
            NotePriority.CRITICAL: '🔴'
        }
        return icons.get(self.priority, '⚪')
    
    def to_dict(self) -> dict:
        """Convert to dictionary for storage"""
        return {
            'text': self.text,
            'author': self.author,
            'timestamp': self.timestamp.isoformat(),
            'category': self.category.value,
            'priority': self.priority.value,
            'mentions': self.mentions,
            'attachments': [str(p) for p in self.attachments],
            'resolved': self.resolved
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'EnhancedNote':
        """Create from dictionary"""
        return cls(
            text=data['text'],
            author=data['author'],
            timestamp=datetime.fromisoformat(data['timestamp']),
            category=NoteCategory(data['category']),
            priority=NotePriority(data['priority']),
            mentions=data.get('mentions', []),
            attachments=[Path(p) for p in data.get('attachments', [])],
            resolved=data.get('resolved', False)
        )


class NotesManager:
    """Manage enhanced notes for shots"""
    
    @staticmethod
    def parse_mentions(text: str) -> List[str]:
        """Extract @mentions from text"""
        import re
        return re.findall(r'@(\w+)', text)
    
    @staticmethod
    def format_note_for_display(note: EnhancedNote) -> str:
        """Format note with all metadata"""
        lines = []
        
        # Header
        time_str = note.timestamp.strftime("%Y-%m-%d %H:%M")
        priority = note.priority.value.upper()
        category = note.category.value.upper()
        
        lines.append(f"{note._get_priority_icon()} [{time_str}] {note.author}")
        lines.append(f"Category: {category} | Priority: {priority}")
        
        # Text
        lines.append(note.text)
        
        # Mentions
        if note.mentions:
            lines.append(f"Mentions: {', '.join('@' + m for m in note.mentions)}")
        
        # Attachments
        if note.attachments:
            lines.append(f"Attachments: {len(note.attachments)} file(s)")
        
        return '\n'.join(lines)
