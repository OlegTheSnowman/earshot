#!/usr/bin/env python3
"""
Re-copy the accessibility signals out of the VS Code install.

The sounds live under a build-hash folder - .../Microsoft VS Code/93cfdd489c/...
- and that hash changes on every VS Code update. Pointing the player straight at
that path would mean the sounds silently stop after an update, with no error and
no obvious cause. So the files are copied into the plugin instead, and this
script re-copies them when the install moves.

    python sync-sounds.py

Prints what it found and what changed. Safe to run any time.
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOUNDS = ROOT / "sounds"

MEDIA_SUFFIX = Path("resources/app/out/vs/platform/accessibilitySignal/browser/media")

# Where VS Code tends to live. The user's is on D:, which no default would guess,
# so the search walks each root looking for the media folder under any build hash.
SEARCH_ROOTS = [
    Path("D:/APPS/Microsoft VS Code"),
    Path.home() / "AppData/Local/Programs/Microsoft VS Code",
    Path("C:/Program Files/Microsoft VS Code"),
    Path("C:/Program Files (x86)/Microsoft VS Code"),
]


def find_media_dir():
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        direct = root / MEDIA_SUFFIX
        if direct.is_dir():
            return direct
        # Newer installs nest everything under a build-hash directory.
        for child in root.iterdir():
            if child.is_dir():
                nested = child / MEDIA_SUFFIX
                if nested.is_dir():
                    return nested
    return None


def main():
    media = find_media_dir()
    if media is None:
        print("Could not find VS Code's accessibility signal folder.")
        print("Looked under:")
        for r in SEARCH_ROOTS:
            print(f"  {r}")
        print("If VS Code moved, add its path to SEARCH_ROOTS in this file.")
        return 1

    print(f"Found: {media}")
    SOUNDS.mkdir(exist_ok=True)

    added, updated, same = 0, 0, 0
    for src in sorted(media.glob("*.mp3")):
        dst = SOUNDS / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            added += 1
        elif src.stat().st_size != dst.stat().st_size:
            shutil.copy2(src, dst)
            updated += 1
        else:
            same += 1

    print(f"{added} added, {updated} updated, {same} unchanged "
          f"({len(list(SOUNDS.glob('*.mp3')))} total in {SOUNDS})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
