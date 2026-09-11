"""
Generate multi-size ICO files from the existing app_icon_128.ico.

Creates:
  - app_icon.ico      (multi-size: 16, 24, 32, 48, 64, 128, 256)
  - app_icon_16.ico   (16x16)
  - app_icon_32.ico   (32x32)
  - app_icon_48.ico   (48x48)
  - app_icon_64.ico   (64x64)
  - app_icon_256.ico  (256x256)
"""

import os
import sys

try:
    from PIL import Image
except ImportError:
    print("Pillow not found, installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
    from PIL import Image

ICONS_DIR = os.path.join(os.path.dirname(__file__), '..', 'ut_vfx', 'icons')
ICONS_DIR = os.path.abspath(ICONS_DIR)
SOURCE = os.path.join(ICONS_DIR, 'app_icon_128.ico')

if not os.path.exists(SOURCE):
    print(f"ERROR: Source icon not found: {SOURCE}")
    sys.exit(1)

# Open the source icon
img = Image.open(SOURCE)
# Convert to RGBA if not already
img = img.convert('RGBA')
print(f"Source: {SOURCE} ({img.size[0]}x{img.size[1]})")

# Define all sizes needed
SIZES = [16, 24, 32, 48, 64, 128, 256]

# Resize using LANCZOS (best quality for downscaling)
resized = {}
for size in SIZES:
    resized[size] = img.resize((size, size), Image.LANCZOS)
    print(f"  Generated {size}x{size}")

# 1. Create the main multi-size app_icon.ico (what the .spec file references)
main_ico = os.path.join(ICONS_DIR, 'app_icon.ico')
# PIL saves multi-size ICO by passing sizes parameter
resized[256].save(
    main_ico,
    format='ICO',
    sizes=[(s, s) for s in SIZES],
    append_images=[resized[s] for s in SIZES if s != 256]
)
print(f"\nCreated: {main_ico}")

# Verify the main ICO has all sizes
verify = Image.open(main_ico)
print(f"  Sizes in ICO: {verify.info.get('sizes', 'N/A')}")

# 2. Create individual size ICO files
for size in SIZES:
    if size == 128:
        continue  # Already exists as app_icon_128.ico
    
    ico_path = os.path.join(ICONS_DIR, f'app_icon_{size}.ico')
    resized[size].save(ico_path, format='ICO', sizes=[(size, size)])
    print(f"Created: {ico_path}")

# 3. Also save PNG versions for GUI use (e.g. window icons, tray icons)
for size in [16, 32, 48, 128, 256]:
    png_path = os.path.join(ICONS_DIR, f'app_icon_{size}.png')
    resized[size].save(png_path, format='PNG')
    print(f"Created: {png_path}")

print(f"\nDone! All icons saved to: {ICONS_DIR}")

# List final contents
print("\nFinal directory contents:")
for f in sorted(os.listdir(ICONS_DIR)):
    full = os.path.join(ICONS_DIR, f)
    print(f"  {f:30s}  {os.path.getsize(full):>8,} bytes")
