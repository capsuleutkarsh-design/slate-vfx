"""
Text utilities — shared helpers for text normalization.

Consolidated from 4+ inline duplicate definitions across tabs.
"""

import re
from pathlib import Path

def normalize_name(s: str) -> str:
    """Normalize a name for fuzzy comparison.

    Strips whitespace, underscores, and hyphens, then lowercases.
    Used for comparing project names, folder names, shot names, etc.

    Example:
        normalize_name("MY_Project-01")  -> "myproject01"
        normalize_name("My Project 01")  -> "myproject01"
    """
    return re.sub(r'[\s_\-]', '', str(s).lower())

def get_resolved_project_root(dest_path_str: str, project_name: str) -> 'Path':
    """
    Intelligently resolves the true project root to prevent duplicate nested folders.
    If the user selects a folder already inside the project (e.g. GG/05_Reels),
    this walks up the tree to return the actual parent root (e.g. the folder containing GG).
    
    Args:
        dest_path_str: The raw destination directory selected by the user.
        project_name: The target project code/name.
        
    Returns:
        Path: The resolved parent directory where the project folder should reside.
    """
    if not dest_path_str or not project_name:
        return Path(dest_path_str) if dest_path_str else Path("")
        
    dest_path = Path(dest_path_str)
    norm_project = normalize_name(project_name)
    
    # Walk up the path hierarchy to see if we are already inside the project folder
    current = dest_path
    while current.parent != current:  # Stop at drive root
        if normalize_name(current.name) == norm_project:
            # We are inside or directly at the project folder. 
            # The *true* target root is the parent of this project folder.
            return current.parent
        current = current.parent
        
    # If project folder isn't in the path, use the selected destination as the root
    return dest_path

