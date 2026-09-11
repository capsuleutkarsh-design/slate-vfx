import logging
# -*- coding: utf-8 -*-
"""Convert emojis in help_content.py to HTML entities"""
import re

# Read the file
with open('core/help_content.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Function to convert emoji to HTML entity
def emoji_to_entity(match):
    char = match.group(0)
    return f'&#{ord(char)};'

# Only convert high Unicode characters (emojis, special symbols)
# Don't convert common punctuation like apostrophes, quotes, etc.
pattern = r'[\u0100-\U0010FFFF]'
count = len(re.findall(pattern, content))

# Replace characters with HTML entities  
result = re.sub(pattern, emoji_to_entity, content)

# Write back
with open('core/help_content.py', 'w', encoding='utf-8') as f:
    f.write(result)

logging.info("Successfully converted emojis to HTML entities!")
logging.info(f"Processed {count} emoji/special characters (preserved ASCII punctuation)")
