
import re
from collections import defaultdict
from pathlib import Path

# BROADER PATTERN: Look for digits at the end of the name, ignoring separator requirement for capture
# But we must be careful not to capture version numbers "v01" as frames if they aren't the final numbers.
# Usually frames are the *last* block of digits.
# Try: ^(.*?)(_|-|\.)?(\d+)(\.[a-zA-Z0-9]+)$
seq_pattern = re.compile(r'^(.*?)(_|-|\.)?(\d+)(\.[a-zA-Z0-9]+)$')

test_filenames = [
    "Explosion_v01.1001.exr",
    "Explosion_v01.1002.exr",
    "shot_010_bg_v001.0001.jpg", 
    "render-0001.png", 
    "comp_v01_0001.tiff",
    "strange.name.1001.exr",
    "no_separator1001.exr", # Should match now
    "no_separator1002.exr",
    "legacyFile0001.dpx",
    "legacyFile0002.dpx",
    "Project_v01.mov" # Should NOT match as sequence (extensions check handles this in code, but regex might match '01')
]

sequences = defaultdict(list)
standalone = []

print("--- TESTING REGEX ---")
for fname in test_filenames:
    match = seq_pattern.match(fname)
    if match:
        # Group 1: Base, Group 2: Sep (Optional), Group 3: Frame, Group 4: Ext
        sep = match.group(2) if match.group(2) else ""
        print(f"[MATCH] {fname} -> Base: '{match.group(1)}' | Sep: '{sep}' | Frame: '{match.group(3)}'")
        sequences[0].append(fname) # Dump all to one list for test simplicity
    else:
        print(f"[FAIL ] {fname}")
        standalone.append(fname)

print("\n--- RESULTS ---")
print(f"Sequences Found: {len(sequences)}")
for (base, ext), frames in sequences.items():
    print(f"  {base}..{ext} ({len(frames)} frames)")

print(f"Standalone Files: {len(standalone)}")
for f in standalone:
    print(f"  {f}")
