# -*- coding: utf-8 -*-
"""
Central repository for all help documentation content.

This module loads help content from a JSON file to avoid Python encoding issues with emojis.
Content is organized by tab/feature with rich formatting, emojis, and examples.
"""

import json
from ..utils.resource_manager import ResourcePathManager

# Load help content from JSON file
# Use ResourcePathManager to find the file in both dev and frozen modes
_json_path = ResourcePathManager.get_resource_path("core/help_content.json")

try:
    with open(_json_path, 'r', encoding='utf-8') as f:
        HELP_CONTENT = json.load(f)
except FileNotFoundError:
    # Fallback if JSON file is missing
    HELP_CONTENT = {
        "getting_started": {
            "title": "Help System",
            "icon": "📚",
            "content": "<h1>Help Content Not Found</h1><p>The help_content.json file is missing.</p>"
        }
    }
except json.JSONDecodeError as e:
    # Fallback if JSON is malformed
    HELP_CONTENT = {
        "getting_started": {
            "title": "Help System Error",
            "icon": "⚠️",
            "content": f"<h1>Help Content Error</h1><p>Error loading help content: {str(e)}</p>"
        }
    }


def get_help_content(tab_id):
    """Get help content for a specific tab."""
    return HELP_CONTENT.get(tab_id, HELP_CONTENT.get("getting_started"))


def _in_mode(section, mode):
    """
    Whether a section belongs in this application.

    Slate ships as two shells over one window - the VFX client and the
    operations shell - and they do not have the same sidebar. Handing an artist
    licence compliance, or somebody in Ops the plate renamer, is the same
    mistake as showing them the tab.

    A section with no "modes" key belongs everywhere, so older content keeps
    working.
    """
    if not mode or mode == "all":
        return True
    modes = section.get("modes")
    if not modes:
        return True
    return mode in modes


def get_all_tabs(mode=None):
    """The help sections for this application, in order."""
    return [
        {"id": key, "title": value["title"], "icon": value.get("icon", "")}
        for key, value in HELP_CONTENT.items()
        if _in_mode(value, mode)
    ]


def search_help(query, mode=None):
    """
    Search help content for keywords.
    Returns list of (tab_id, title, snippet) tuples.
    """
    query = query.lower()
    results = []
    
    for tab_id, content_data in HELP_CONTENT.items():
        # Scoped to the sections this application is showing - a hit on a
        # page it does not display is a dead end.
        if not _in_mode(content_data, mode):
            continue
        content = content_data.get("content", "")
        if query in content.lower() or query in content_data["title"].lower():
            # Extract snippet around match
            idx = content.lower().find(query)
            snippet_start = max(0, idx - 50)
            snippet_end = min(len(content), idx + 100)
            snippet = content[snippet_start:snippet_end].strip()
            results.append((
                tab_id,
                content_data["title"],
                f"...{snippet}..."
            ))
    
    return results
