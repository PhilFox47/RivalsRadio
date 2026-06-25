"""Regenerate assets/icon.ico from assets/icon.png.

The PNG is the source of truth for the app icon — replace it with your own
artwork and this produces the multi-resolution .ico the build/window use.
Run:  python scripts/png_to_ico.py
"""

import os
import sys

from PIL import Image

SRC = sys.argv[1] if len(sys.argv) > 1 else "assets/icon.png"
DST = sys.argv[2] if len(sys.argv) > 2 else "assets/icon.ico"

img = Image.open(SRC).convert("RGBA")
img.save(DST, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print(f"wrote {DST} ({os.path.getsize(DST)} bytes) from {SRC}")
