import logging
# -*- coding: utf-8 -*-
"""Update tester tab documentation to be accurate"""
import json

with open('core/help_content.json', 'r', encoding='utf-8') as f:
    help_data = json.load(f)

# Replace tester tab with accurate content
help_data["tester"] = {
    "title": "🧪 Tester Tab",
    "icon": "🧪",
    "content": """
<h1>🧪 Tester Panel</h1>

<h2>Overview</h2>
<p>Developer and QA tool for generating test data, simulating production workflows, and validating the Smart Ingest system. Create realistic file structures to test organizational rules without affecting real production data.</p>

<h3>📁 File Generator</h3>
<p>Generate test files of various types and sizes:</p>
<ul>
<li><b>File Count</b> - Number of files to create (1-1000)</li>
<li><b>File Types</b> - .mov, .jpg, .exr, .dpx, .mp4, etc.</li>
<li><b>Size Strategy</b> - Empty (0 KB), Small (1-10 MB), Large (100+ MB)</li>
<li><b>Target Path</b> - Where to create test files</li>
</ul>

<h3>🎬 Workflow Simulator</h3>
<p>Simulate real-world VFX delivery scenarios:</p>
<ul>
<li><b>Delivery Structures</b> - Creates realistic folder hierarchies matching client deliveries</li>
<li><b>Sequence Pattern</b> - Auto-generates frame sequences (e.g., file.####.exr)</li>
<li><b>Shot Folders</b> - Simulates multiple shots with proper naming conventions</li>
<li><b>Mixed Content</b> - Plates, renders, comp files in appropriate subfolders</li>
</ul>

<h3>📊 Smart Analyzer & Validator</h3>
<p>Test and validate Smart Ingest results:</p>
<ul>
<li><b>Run Analyzer</b> - Test the Smart Ingest engine on generated test data</li>
<li><b>Compare Results</b> - Verify files ended up in correct destinations</li>
<li><b>Mode Selection</b> - Test "Raw Copy" vs "Smart Ingest" modes</li>
<li><b>Database Verification</b> - Confirm all operations logged correctly</li>
</ul>

<h3>🗂️ Structure Generator</h3>
<p>Create complex folder hierarchies:</p>
<ul>
<li><b>Nesting Level</b> - How deep to create folders (1-10 levels)</li>
<li><b>Folder Count</b> - Number of folders per level</li>
<li><b>Naming Patterns</b> - SEQ###, SH###, etc.</li>
</ul>

<h3>🛠️ Utilities</h3>
<ul>
<li><b>Database Vacuum</b> - Optimize database, reclaim space</li>
<li><b>Integrity Check</b> - Verify database health</li>
<li><b>Cache Clear</b> - Wipe thumbnail and metadata caches</li>
<li><b>Permissions Test</b> - Verify user role permissions</li>
<li><b>Config Sandbox</b> - Test configuration changes in isolation</li>
</ul>

<h2>How to Use</h2>

<h3>Testing Smart Ingest</h3>
<ol>
<li><b>Generate Test Data</b> - Use File Generator tab to create test files</li>
<li><b>Create Structure</b> - Use Workflow Simulator to create realistic delivery structure</li>
<li><b>Run Smart Ingest</b> - Go to Smart Ingest tab, select test folder as source</li>
<li><b>Validate</b> - Return to Tester, use Analyzer to verify results</li>
<li><b>Clean Up</b> - Use "Wipe Folder" to delete test data when done</li>
</ol>

<h3>Generating a Test Delivery</h3>
<ol>
<li>Go to <b>Workflow Simulator</b> tab</li>
<li>Set root path (e.g., your system's temp folder)</li>
<li>Configure file count, types (e.g., 50 files, .exr + .mov)</li>
<li>Click <b>Simulate Workflow</b></li>
<li>Wait for generation to complete</li>
<li>Check log panel for created structure details</li>
</ol>

<h2>💡 Tips & Tricks</h2>
<p><b>🎯 Quick Test</b>: Use "Empty" size strategy for faster generation (0 KB files)</p>
<p><b>⚡ Realistic Tests</b>: Use "Large" size strategy to test performance with real file sizes</p>
<p><b>📝 Log Review</b>: Check log panel at bottom for detailed operation info</p>
<p><b>🧹 Clean Testing</b>: Always use a temp location, never production paths</p>

<h2>⚠️ Important Notes</h2>
<p><b>Test Data Only</b>: Never point this tool at production folders - it generates/deletes files!</p>
<p><b>Permissions</b>: Requires Developer or Admin role to access this tab</p>
<p><b>Performance</b>: Generating 1000+ large files may take several minutes</p>
<p><b>Database Impact</b>: Validation with DB check will add entries to your database - use test DB if available</p>
"""
}

# Write back
with open('core/help_content.json', 'w', encoding='utf-8') as f:
    json.dump(help_data, f, ensure_ascii=False, indent=2)

logging.info("Updated tester tab with accurate documentation")
logging.info("Total tabs:", len(help_data))
