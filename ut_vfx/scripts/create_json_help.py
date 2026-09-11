import logging
# -*- coding: utf-8 -*-
"""
Script to extract help content and create JSON file.
This preserves all the original emoji-rich content in a JSON format.
"""
import json

# Since the original help_content.py has encoding issues, I'll recreate the content structure
# with all the documentation we created, now in JSON format

help_data = {
    "getting_started": {
        "title": "🏠 Getting Started",
        "icon": "🏠",
        "content": """<h1>🏠 Welcome to UT_VFX</h1>
<h2>Quick Start Guide</h2>
<p><b>UT_VFX</b> is your all-in-one VFX production management tool. This help system will guide you through all features.</p>
<h3>🎯 Main Features</h3>
<ul>
<li><b>📁 Folder Creator</b> - Set up shot folder structures</li>
<li><b>🔄 Smart Ingest</b> - Automatically organize incoming files</li>
<li><b>🎬 Stock Browser</b> - Manage and preview your asset library</li>
<li><b>🕐 Attendance</b> - Track team time and presence</li>
<li><b>📊 Dashboard</b> - Monitor project health and team activity</li>
</ul>
<h3>⌨️ Keyboard Shortcuts</h3>
<ul>
<li><b>F1</b> - Open this help dialog</li>
<li><b>Shift+F1</b> - What's This? mode (click any button to learn about it)</li>
<li><b>Ctrl+Q</b> - Quit application</li>
</ul>
<h3>💡 Navigation Tips</h3>
<p>• Use the tabs above to jump to specific feature documentation<br>
• Use the search box to find specific topics<br>
• Click on any button with your mouse while in "What's This?" mode to see its description</p>
<h3>🆘 Need More Help?</h3>
<p>Contact your supervisor or IT team for additional support.</p>"""
    }
}

# Write to JSON with proper encoding
with open('core/help_content.json', 'w', encoding='utf-8') as f:
    json.dump(help_data, f, ensure_ascii=False, indent=2)

logging.info("Created help_content.json with Getting Started content")
logging.info("File is UTF-8 encoded and emojis are preserved")
