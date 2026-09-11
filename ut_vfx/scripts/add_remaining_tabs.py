import logging
# -*- coding: utf-8 -*-
"""
Add remaining 8 tabs to help_content.json with comprehensive documentation.
"""
import json

# Load existing content
with open('core/help_content.json', 'r', encoding='utf-8') as f:
    help_data = json.load(f)

# Add remaining tabs with comprehensive content
help_data["folder_creator"] = {
    "title": "📁 Folder Creator",
    "icon": "📁",
    "content": """
<h1>📁 Folder Creator</h1>
<h2>Overview</h2>
<p>Automate creation of standardized VFX shot folder structures with one click.</p>
<h3>🎬 Shot Management</h3>
<ul>
<li><b>Batch Creation</b> - Create multiple shots at once (e.g., SH010-SH050)</li>
<li><b>Template System</b> - Use predefined folders templates or create custom ones</li>
<li><b>Naming Conventions</b> - Enforces studio naming standards automatically</li>
</ul>
<h3>📋 Templates</h3>
<ul><li>Standard VFX - Full shot with all departments</li><li>Comp Only - Lightweight comp structure</li><li>CG Only - 3D/CG focused structure</li><li>Custom - Define your own templates</li></ul>
<h2>How to Use</h2>
<h3>Creating a Single Shot</h3>
<ol><li>Select Project from dropdown</li><li>Enter Sequence (e.g., "SEQ010")</li><li>Enter Shot Number (e.g., "010")</li><li>Choose Template</li><li>Preview Structure</li><li>Click CREATE</li></ol>
<h2>💡 Tips</h2>
<p><b>Quick Create</b>: Press Enter after typing shot number to immediately create</p>
<p><b>Clipboard Support</b>: Paste shot list from Excel directly into batch field</p>
"""
}

help_data["smart_ingest"] = {
    "title": "🔄 Smart Ingest",
    "icon": "🔄",
    "content": """
<h1>🔄 Smart Ingest / Move & Scan</h1>
<h2>Overview</h2>
<p>Automatically analyze incoming folders, detect sequences, shots, and file types, then organize into your project structure.</p>
<h3>🧠  Intelligent Analysis</h3>
<ul>
<li>Sequence Detection - Auto-detects image sequences</li>
<li>Shot Recognition - Identifies shot folders from naming patterns</li>
<li>Reel Detection - Recognizes container folders</li>
<li>Category Classification - Auto-sorts into plates/, renders/, comp/, etc.</li>
</ul>
<h3>📊 Detailed Reports</h3>
<ul><li>Pre-Scan Preview - See what will happen before moving</li><li>Conflict Detection - Warns about duplicates</li><li>Statistics - Total files, sequences, shots detected</li><li>HTML Report - Exportable summary</li></ul>
<h2>How to Use</h2>
<ol><li>Select Source Folder - Click Browse</li><li>Select Destination Project</li><li>Click ANALYZE - System scans and shows preview</li><li>Review Report</li><li>Click EXECUTE - Files organized</li></ol>
"""
}

help_data["incoming_delivery"] = {
    "title": "📥 Incoming Delivery",
    "icon": "📥",
    "content": """
<h1>📥 Incoming Delivery</h1>
<h2>Overview</h2>
<p>Streamlined workflow for processing client deliveries with automatic validation.</p>
<h3>📦 Delivery Processing</h3>
<ul><li>Automatic Scanning - Monitors incoming folder</li><li>Validation Checks - Verifies file integrity, naming, format</li><li>Metadata Extraction - Reads embedded shot info, timecode</li><li>Color Space Detection - Identifies LOG, REC709, ACES</li></ul>
<h3>🔍 Quality Checks</h3>
<ul><li>Resolution Verification</li><li>Frame Rate Validation</li><li>Missing Frame Detection</li><li>Corrupt File Detection</li></ul>
<h2>How to Use</h2>
<ol><li>Set Incoming Folder in Settings</li><li>Client places files in folder</li><li>Auto-Scan Triggers</li><li>Review Report</li><li>Accept or Reject delivery</li><li>Files Auto-Organize if accepted</li></ol>
"""
}

help_data["rename_tool"] = {
    "title": "✏️ Rename Tool",
    "icon": "✏️",
    "content": """
<h1>✏️ Rename Tool (Cap Rename)</h1>
<h2>Overview</h2>
<p>Batch rename files and sequences with powerful pattern matching and variable substitution.</p>
<h3>🔤 Naming Patterns</h3>
<ul><li>Variable Substitution - Use {SHOT}, {SEQ}, {FRAME}, {EXT}</li><li>Regex Support - Advanced pattern matching</li><li>Padding Control - Specify frame number padding (4, 5, 6 digits)</li><li>Case Conversion - UPPERCASE, lowercase, Title Case</li></ul>
<h3>📝 Preview & Undo</h3>
<ul><li>Live Preview - See results before applying</li><li>Before/After Table - Compare old vs new names</li><li>Conflict Detection - Warns about name collisions</li><li>Undo Support - Revert if needed</li></ul>
<h2>How to Use</h2>
<ol><li>Select Files to rename</li><li>Define Pattern - Enter new naming pattern</li><li>Preview - Check before/after table</li><li>Apply - Rename all files</li></ol>
<p><b>Example:</b> {SHOT}_{CATEGORY}_{FRAME}.{EXT}</p>
"""
}

help_data["dashboard"] = {
    "title": "📊 Dashboard",
    "icon": "📊",
    "content": """
<h1>📊 Dashboard</h1>
<h2>Overview</h2>
<p>Real-time overview of project health, team activity, and system status. Monitor everything from one central view.</p>
<h3>📈 Project Metrics</h3>
<ul><li>Shot Progress - Visual breakdown by status (Assigned, In Progress, Review, Final)</li><li>Completion Percentage - Overall project completion</li><li>Burn Down Chart - Track shots completed over time</li><li>Deadline Tracking - Days to delivery, at-risk shots</li></ul>
<h3>👥 Team Activity</h3>
<ul><li>Who's Working - Live view of active artists</li><li>Attendance Overview - Who's in, who's out, who's WFH</li><li>Workload Distribution - Shots per artist</li><li>Activity Feed - Recent file updates, renders, publishes</li></ul>
<h3>🖥️ System Health</h3>
<ul><li>Render Farm Status - Active nodes, queue size</li><li>Storage Usage - Network drive space remaining</li><li>Database Status - Connection health, response time</li><li>Cache Health - Thumbnail cache, proxy cache status</li></ul>
<h2>💡 Tips</h2>
<p><b>Pin Widgets</b>: Customize layout by pinning frequently-used widgets</p>
<p><b>Auto-Refresh</b>: Dashboard updates every 30 seconds automatically</p>
<p><b>Export Reports</b>: Generate PDF reports for client updates</p>
"""
}

help_data["attendance"] = {
    "title": "🕐 Attendance",
    "icon": "🕐",
    "content": """
<h1>🕐 Attendance</h1>
<h2>Overview</h2>
<p>Track team presence with automatic login, manual punch in/out, and comprehensive reporting.</p>
<h3>⏰ Time Tracking</h3>
<ul><li>Auto Login - App launch triggers automatic punch-in</li><li>Manual Punch - Override/manual punch in/out buttons</li><li>WFH Tracking - Mark work-from-home days</li><li>Auto-Logout - If you forget to logout, system auto-registers at 7:30 PM on next login</li></ul>
<h3>📊 Team Overview (Admin)</h3>
<ul><li>Monthly Grid - See all team members' attendance</li><li>Color Coding - Green=On time, Yellow=Late, Red=Absent, Blue=WFH</li><li>Statistics - Total hours, late count, on-time streak per person</li><li>Edit Capability - Admins can fix incorrect entries</li></ul>
<h3>🏆 Streaks & Stats</h3>
<ul><li>On-Time Streak - Consecutive days arriving before 10:45 AM</li><li>Total Hours - Monthly work hours</li><li>Late Tracking - Count of late arrivals</li><li>Perfect Attendance - Badge for full month on-time</li></ul>
<h2>How to Use</h2>
<h3>Punching In/Out</h3>
<ol><li>Auto Punch-In - Happens on app launch (3 second delay)</li><li>Manual Punch - Click IN or OUT button if needed</li><li>WFH Mode - Check "Work From Home" before punching in</li><li>View Status - Personal view shows your current status and streak</li></ol>
<h2>💡 Tips</h2>
<p><b>Streak Building</b>: Arrive before 10:45 AM to maintain on-time streak</p>
<p><b>Monthly Review</b>: Export CSV at month-end for payroll/reporting</p>
<p><b>WFH Tracking</b>: Always check WFH box when working remotely</p>
"""
}

help_data["settings"] = {
    "title": "⚙️ Settings",
    "icon": "⚙️",
    "content": """
<h1>⚙️ Settings</h1>
<h2>Overview</h2>
<p>Configure application behavior, paths, preferences, and visual themes. Settings saved per-user and persist across sessions.</p>
<h3>🎨 Appearance</h3>
<ul><li>Theme Selection - Dark, Darker, Nord, Dracula, etc.</li><li>Font Size - Small, Medium, Large</li><li>Accent Color - Customize highlight colors</li><li>Icon Pack - Choose icon style</li></ul>
<h3>📁 Paths & Locations</h3>
<ul><li>Default Project Path - Where projects are stored</li><li>Stock Library Path - Network location of stock assets</li><li>Render Output Path - Default render destination</li><li>Temp/Cache Path - Local cache location</li></ul>
<h3>🔧 Behavior</h3>
<ul><li>Auto-Save Interval - Frequency of auto-saving (minutes)</li><li>Thumbnail Quality - Low, Medium, High (affects performance)</li><li>Max Recent Files - Number of recent items to remember</li><li>Startup Mode - Which tab opens by default</li></ul>
<h3>🌐 Network & Sync</h3>
<ul><li>Server Address - Central database server</li><li>Sync Interval - How often to check for updates</li><li>Offline Mode - Work without network connection</li><li>Cache Strategy - Aggressive, Normal, Minimal</li></ul>
<h2>How to Use</h2>
<ol><li>Navigate to Settings tab</li><li>Select category from left sidebar</li><li>Modify desired settings</li><li>Click SAVE or APPLY</li><li>Some settings require app restart</li></ol>
"""
}

help_data["admin_panel"] = {
    "title": "👤 Admin Panel",
    "icon": "👤",
    "content": """
<h1>👤 Admin Panel</h1>
<h2>Overview</h2>
<p>Centralized user management, permissions, system configuration, and audit logs. Requires Administrator or Lead role to access.</p>
<h3>👥 User Management</h3>
<ul><li>Add Users - Create new user accounts</li><li>Edit Profiles - Update names, emails, profile pictures</li><li>Role Assignment - Artist, Lead, Supervisor, Admin</li><li>Deactivate Users - Disable access without deleting account</li></ul>
<h3>🔐 Permissions</h3>
<ul><li>Role-Based Access - Define what each role can do</li><li>Project Access - Control which users see which projects</li><li>Feature Toggles - Enable/disable features per user/role</li><li>Audit Trail - Log all permission changes</li></ul>
<h3>🗄️ System Config</h3>
<ul><li>Global Paths - Set network drive mappings</li><li>Database Settings - Connection strings, backup schedule</li><li>Email Notifications - Configure SMTP, recipients</li><li>License Management - View/update license keys</li></ul>
<h3>📒 Audit Logs</h3>
<ul><li>User Activity - Login/logout, file operations</li><li>System Events - Crashes, errors, warnings</li><li>Permission Changes - Who changed what and when</li><li>Export Logs - Download for external analysis</li></ul>
<h2>How to Use</h2>
<h3>Adding a New User</h3>
<ol><li>Click Users tab</li><li>Click + ADD USER button</li><li>Fill in details (Username, Full Name, Email, Role)</li><li>Set initial password (user changes on first login)</li><li>Assign project access</li><li>Click CREATE</li></ol>
<h2>Roles & Permissions</h2>
<p><b>Artist</b>: View assigned shots, update files, basic attendance</p>
<p><b>Lead</b>: + Create shots, assign tasks, view team attendance</p>
<p><b>Supervisor</b>: + Edit attendance, manage projects, view all shots</p>
<p><b>Admin</b>: + User management, system config, full access</p>
"""
}

# Write updated JSON
with open('core/help_content.json', 'w', encoding='utf-8') as f:
    json.dump(help_data, f, ensure_ascii=False, indent=2)

logging.info("Updated help_content.json")
logging.info(f"Total tabs: {len(help_data)}")
logging.info("File size:", len(json.dumps(help_data, ensure_ascii=False)) // 1024, "KB")
