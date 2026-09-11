"""
SECURE Smart Ingest Logic Engine.
Handles intelligent categorization of files/folders based on aliases, extensions, and context rules.
"""
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

class SmartIngestAnalyzer:
    """
    Analyzes paths to determine the most likely target category based on weighted confidence rules.
    """
    def __init__(self, ingest_rules: Dict[str, Any]):
        self.rules = ingest_rules
        self._compile_rules()

    def _compile_rules(self):
        """Pre-process rules for faster matching."""
        self.compiled_rules = {}
        for category, data in self.rules.items():
            self.compiled_rules[category] = {
                "aliases": [a.lower() for a in data.get("aliases", [])],
                "extensions": [e.lower() for e in data.get("extensions", [])],
                "priority": data.get("priority", 0)
            }

    def analyze_item(self, path: Path) -> Tuple[Optional[str], float, str]:
        """
        Analyze a file or folder path and return prediction.
        
        Returns:
            Tuple[Optional[str], float, str]: (Category Name, Confidence Score, Match Reason)
            Confidence Score: 0.0 - 1.0 (1.0 = Certain)
        """
        best_category = None
        best_score = 0.0
        match_reason = "No Match"
        
        normalized_name = self._normalize_name(path.name)
        
        # 1. Direct Name Match (Strongest - Priority 100+)
        # If folder name is "roto" or "cmp", it dictates the content.
        cat, score, reason = self._check_name_match(normalized_name)
        if score > best_score:
            best_category = cat
            best_score = score
            match_reason = reason
            
        # If we have a very strong name match, return immediately
        if best_score >= 0.9:
            return best_category, best_score, match_reason

        # 2. Parent Context Match (Medium - Priority 80+)
        # If path is "incoming/roto/shot01.exr", parent "roto" is key.
        parent_name = self._normalize_name(path.parent.name)
        cat, score, reason = self._check_name_match(parent_name, is_parent=True)
        if score > best_score:
            best_category = cat
            best_score = score
            match_reason = reason
            
        if best_score >= 0.8:
            return best_category, best_score, match_reason

        # 3. Extension fallback (Weakest - Priority 50)
        # Only check file extension if it's a file
        if path.is_file():
            ext = path.suffix.lower()
            cat, score, reason = self._check_extension_match(ext)
            
            # Special case: If we already have a weak name match (e.g. 0.4), 
            # and extension matches a DIFFERENT category, we need to decide.
            # But currently we only set best_score if strict name match failed.
            if score > best_score:
                best_category = cat
                best_score = score
                match_reason = reason
        
        return best_category, best_score, match_reason

    def _normalize_name(self, name: str) -> str:
        """Strip versions, dates, and common extraneous chars."""
        # Lowercase
        name = name.lower()
        # Remove version numbers like _v01, .v003
        name = re.sub(r'[._]v\d+', '', name)
        # Remove shot patterns loosely (shot01_cmp -> cmp)
        # This is hard to do generically without destroying valid aliases, 
        # so we rely on substring searching in aliases instead.
        return name

    def _check_name_match(self, name_segment: str, is_parent=False) -> Tuple[Optional[str], float, str]:
        """Check if name segment matches any aliases."""
        best_cat = None
        best_prio = -1
        
        for category, rules in self.compiled_rules.items():
            current_prio = rules['priority']
            
            for alias in rules['aliases']:
                # Exact Match or Word Boundary Match
                # e.g. "roto" matches "shot01_roto_v1"
                if alias == name_segment or f"_{alias}_" in f"_{name_segment}_" or f"_{alias}" in name_segment or f"{alias}_" in name_segment:
                    
                    # Logic: Allow higher priority rules to override lower ones
                    # e.g. "clean_plate" -> "clean" (Prep/20) vs "plate" (Plate/10). Prep wins.
                    if current_prio > best_prio:
                        best_cat = category
                        best_prio = current_prio
                        
        if best_cat:
            context = "Parent Folder" if is_parent else "Folder/File Name"
            return best_cat, 0.9 if not is_parent else 0.8, f"Matched {context} Alias"
            
        return None, 0.0, ""

    def _check_extension_match(self, ext: str) -> Tuple[Optional[str], float, str]:
        """Check if extension matches any rules."""
        # Extensions are ambiguous (exr in Prep, Plate, Lighting).
        # We use priority to break ties.
        
        best_cat = None
        best_prio = -1
        
        for category, rules in self.compiled_rules.items():
            if ext in rules['extensions']:
                if rules['priority'] > best_prio:
                    best_cat = category
                    best_prio = rules['priority']
                    
        if best_cat:
            return best_cat, 0.5, f"Matched Extension {ext} (Priority {best_prio})"
            
        return None, 0.0, ""