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


def get_all_tabs():
    """Get list of all help tabs."""
    return [
        {"id": key, "title": value["title"], "icon": value["icon"]}
        for key, value in HELP_CONTENT.items()
    ]


def search_help(query):
    """
    Search help content for keywords.
    Returns list of (tab_id, title, snippet) tuples.
    """
    query = query.lower()
    results = []
    
    for tab_id, content_data in HELP_CONTENT.items():
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
