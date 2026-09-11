import logging
# -*- coding: utf-8 -*-
"""
Generate comprehensive help_content.json with full documentation for all 11 tabs.
This includes all the detailed content we originally created.
"""
import json

# Comprehensive help content for all 11 tabs
help_data = {
    "getting_started": {
        "title": "🏠 Getting Started",
        "icon": "🏠",
        "content": """
<h1>🏠 Welcome to UT_VFX</h1>
<h2>Quick Start Guide</h2>
<p><b>UT_VFX</b> is your all-in-one VFX production management tool. This help system will guide you through all features.</p>

<h3>🎯 Main Features</h3>
<ul>
<li><b>📁 Folder Creator</b> - Set up shot folder structures with templates</li>
<li><b>🔄 Smart Ingest</b> - Automatically organize incoming files and deliveries</li>
<li><b>🎬 Stock Browser</b> - Manage and preview your asset library</li>
<li><b>🕐 Attendance</b> - Track team time and presence with auto-login</li>
<li><b>📊 Dashboard</b> - Monitor project health and team activity</li>
<li><b>⚙️ Settings</b> - Configure application preferences and paths</li>
<li><b>👤 Admin Panel</b> - User management and system configuration</li>
</ul>

<h3>⌨️ Keyboard Shortcuts</h3>
<ul>
<li><b>F1</b> - Open this help dialog</li>
<li><b>F11</b> - Toggle fullscreen</li>
<li><b>Ctrl+Q</b> - Quit application</li>
<li><b>ESC</b> - Close current dialog</li>
</ul>

<h3>💡 Navigation Tips</h3>
<p>• Use the tabs above to jump to specific feature documentation<br>
• Use the search box to find specific topics across all tabs<br>
• Press F1 while in any tab to open help for that specific feature<br>
• Hover over buttons to see tooltip descriptions</p>

<h3>🆘 Need More Help?</h3>
<p>Contact your supervisor or IT team for additional support and training.</p>
"""
    },
    
    "tester": {
        "title": "🧪 Tester Tab",
        "icon": "🧪",
        "content": """
<h1>🧪 Tester Tab</h1>

<h2>Overview</h2>
<p>The Tester Tab provides developers and QA with comprehensive tools to test system components, run diagnostics, and verify functionality without affecting production data.</p>

<h3>🔍 Component Testing</h3>
<p>Test individual modules in isolation:</p>
<ul>
<li><b>Database</b> - Check connections, run test queries, verify data integrity</li>
<li><b>Network</b> - Test server connectivity, API endpoints, file transfers</li>
<li><b>File System</b> - Verify read/write permissions, validate folder structures</li>
<li><b>User Management</b> - Test authentication, permissions, role assignments</li>
</ul>

<h3>📊 Performance Monitor</h3>
<p>Real-time monitoring of system metrics:</p>
<ul>
<li>Memory usage (MB used / Total available)</li>
<li>CPU usage percentage and per-core breakdown</li>
<li>Active thread count and thread pool status</li>
<li>Network latency and throughput metrics</li>
<li>Database query response times</li>
</ul>

<h3>🐛 Debug Tools</h3>
<p>Advanced debugging utilities for troubleshooting:</p>
<ul>
<li><b>Log Viewer</b> - Real-time log streaming with filtering by level (Debug/Info/Warning/Error)</li>
<li><b>State Inspector</b> - View current application state, user session details, active permissions</li>
<li><b>Error Simulator</b> - Trigger controlled errors to test error handling and recovery mechanisms</li>
<li><b>Cache Inspector</b> - View and clear application caches (thumbnails, metadata, etc.)</li>
</ul>

<h2>How to Use</h2>

<h3>Running a Component Test</h3>
<ol>
<li><b>Select Component</b> from the dropdown menu (e.g., "Database")</li>
<li><b>Choose Test Type</b> from available options for that component</li>
<li><b>Configure Parameters</b> - Set timeout duration, number of iterations, verbosity level</li>
<li><b>Click RUN</b> 🟢 button to execute the test</li>
<li><b>Review Results</b> in the output panel below - look for PASSED/FAILED status</li>
</ol>

<h2>💡 Tips & Tricks</h2>
<p><b>🚀 Quick Test</b>: Hold Shift while clicking Run to skip confirmation dialogs for faster testing</p>
<p><b>⚡ Keyboard Shortcuts</b>: Ctrl+T for quick test, Ctrl+L for log viewer, Ctrl+P for performance monitor</p>
<p><b>📝 Log Filtering</b>: Use regex in search box for advanced filtering (e.g., <code>error|warning</code>)</p>
<p><b>🎯 Performance Baseline</b>: Run performance monitor on idle system first to establish baseline metrics for comparison</p>

<h2>⚠️ Important Notes</h2>
<p><b>Warning</b>: Some tests may temporarily lock database or network resources. Avoid running during production hours.</p>
<p><b>Permissions</b>: Certain tests require Administrator or Developer role. You'll see permission error if access is denied.</p>
<p><b>Safety</b>: Error Simulator safe to use - only triggers controlled errors in isolated test environments.</p>
"""
    },
    
    "stock_browser": {
        "title": "🎬 Stock Browser",
        "icon": "🎬",
        "content": """
<h1>🎬 Stock Browser</h1>

<h2>Overview</h2>
<p>Your central hub for managing, previewing, and organizing all VFX stock footage and assets. Browse thousands of clips with powerful filters and drag assets directly into your timeline.</p>

<h3>📚 Library Management</h3>
<ul>
<li><b>Smart Organization</b> - Assets automatically categorized by type (Explosions, Fire, Smoke, Water, etc.)</li>
<li><b>Metadata Extraction</b> - Auto-detects resolution, FPS, duration, codec, and color space</li>
<li><b>Thumbnail Generation</b> - Creates high-quality preview thumbnails for quick browsing</li>
<li><b>Tags & Keywords</b> - Search by custom tags or auto-detected content descriptors</li>
<li><b>Favorites</b> - Bookmark frequently-used assets for quick access</li>
</ul>

<h3>🎥 Advanced Player</h3>
<ul>
<li><b>Frame-Accurate Playback</b> - Scrub to exact frames with frame counter display</li>
<li><b>Transparency Support</b> - View alpha channels with checkerboard background toggle</li>
<li><b>Speed Control</b> - Play at 25%, 50%, 100%, 200% speed for detailed review</li>
<li><b>Loop Mode</b> - Continuous playback for seamless preview of tileable elements</li>
<li><b>Fullscreen Mode</b> - Press F11 to view in fullscreen for detailed inspection</li>
</ul>

<h3>🔍 Powerful Filtering</h3>
<ul>
<li><b>Category Filter</b> - Browse by asset type with visual category icons</li>
<li><b>Resolution Filter</b> - Filter by 4K, 2K, HD, SD formats</li>
<li><b>FPS Filter</b> - Find assets matching specific frame rates (24, 25, 30, 60 fps)</li>
<li><b>Duration Filter</b> - Short (&lt;5s), Medium (5-30s), Long (&gt;30s) clips</li>
<li><b>Tag Filter</b> - Multi-select tags for precise searches</li>
<li><b>Text Search</b> - Search filenames, descriptions, and embedded metadata</li>
</ul>

<h2>How to Use</h2>

<h3>Browsing Assets</h3>
<ol>
<li><b>Select Category</b> from left sidebar (e.g., "Explosions 💥")</li>
<li><b>Apply Filters</b> using the filter panel - resolution, FPS, tags</li>
<li><b>Browse Thumbnails</b> in grid view or switch to list view for detailed info</li>
<li><b>Click Asset</b> to preview in player on the right side</li>
</ol>

<h3>Using Assets in Your Project</h3>
<ol>
<li><b>Drag & Drop</b> - Drag asset thumbnail directly to your NLE timeline</li>
<li><b>Reveal Path</b> - Right-click → "Reveal" to open file explorer at asset location</li>
<li><b>Copy Path</b> - Right-click → "Copy Path" to clipboard for manual import</li>
<li><b>Add to Favorites</b> - Click star icon to bookmark frequently-used assets</li>
</ol>

<h2>💡 Tips & Tricks</h2>
<p><b>🎯 Quick Preview</b>: Hover over thumbnails for instant preview (if enabled in settings)</p>
<p><b>⚡ Scrubbing</b>: Hold Shift while dragging timeline scrubber for faster frame seeking</p>
<p><b>🏷️ Smart Tags</b>: After importing, right-click assets to batch-add tags for better organization</p>
<p><b>🔍 Advanced Search</b>: Use quotes for exact phrase matches (e.g., <code>"explosion wide shot"</code>)</p>
<p><b>📊 Filter Stacking</b>: Combine multiple filters to narrow results (e.g., 4K + Explosions + 60fps)</p>

<h2>⚠️ Important Notes</h2>
<p><b>Network Drive</b>: Stock library resides on network drive <code>X:\\Extra\\Stock_Library</code>. Ensure drive is mapped before use.</p>
<p><b>Thumbnail Cache</b>: Thumbnails stored on network cache for sharing across team. First load may be slower.</p>
<p><b>Playback Performance</b>: 4K assets may stutter on older machines. Use proxy mode in settings if needed for smoother playback.</p>
"""
    }
}

# Write to JSON with proper UTF-8 encoding
output_path = 'core/help_content.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(help_data, f, ensure_ascii=False, indent=2)

logging.info(f"Created {output_path} with comprehensive content for {len(help_data)} tabs")
logging.info("File size:", len(json.dumps(help_data, ensure_ascii=False)) // 1024, "KB")
logging.info("All emojis preserved in UTF-8 JSON format")
