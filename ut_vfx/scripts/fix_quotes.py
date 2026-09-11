import logging
# -*- coding: utf-8 -*-
"""Fix apostrophes in help_content.py"""

# Read the file
with open('core/help_content.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace all curly apostrophes with straight ones
content = content.replace("'", "'")  # Right single quotation mark to apostrophe
content = content.replace("'", "'")  # Left single quotation mark to apostrophe  
content = content.replace(""", '"')  # Left double quotation mark
content = content.replace(""", '"')  # Right double quotation mark

# Write back
with open('core/help_content.py', 'w', encoding='utf-8') as f:
    f.write(content)

logging.info("Successfully normalized quotes in help_content.py!")
