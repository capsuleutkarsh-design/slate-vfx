import urllib.request
import os
import ssl

# Ignore SSL errors
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

TARGET_DIR = r".\ut_vfx\resources\web\univer\lib"

# URLs for Univer Presets (UMD build)
FILES = {
    "univer.full.js": "https://unpkg.com/@univerjs/presets@0.1.0-beta.2/lib/umd/index.js",
    "univer.css": "https://unpkg.com/@univerjs/presets@0.1.0-beta.2/lib/styles/index.css"
}

print(f"Downloading to: {TARGET_DIR}")

if not os.path.exists(TARGET_DIR):
    os.makedirs(TARGET_DIR)
    print("Created directory.")

for filename, url in FILES.items():
    dest = os.path.join(TARGET_DIR, filename)
    print(f"Downloading {filename}...")
    try:
        with urllib.request.urlopen(url, context=ctx) as response, open(dest, 'wb') as out_file:
            data = response.read()
            out_file.write(data)
            print(f"Success! {len(data)} bytes written.")
    except Exception as e:
        print(f"FAILED to download {filename}: {e}")

print("Download complete.")
