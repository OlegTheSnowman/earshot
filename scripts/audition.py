#!/usr/bin/env python3
"""
Play every sound in the pack, speaking its name first.

    python audition.py              # all 31
    python audition.py mapped       # only the ones currently wired to an event
    python audition.py unmapped     # only the spare ones

A printed list is no use for choosing a sound, and a bare sequence of 31 chimes
is no use either - by the fourth you have lost track of which was which. So the
name is spoken through the Windows voice, then the sound plays, then it waits.

Unlike play.py this one blocks on purpose. It is run by hand, not by a hook.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOUNDS = ROOT / "sounds"
CONFIG = ROOT / "config.json"


def say(text):
    """Speak through the built-in Windows voice, waiting until it finishes."""
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.Speak('{text}')"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True)


def play_blocking(path, volume):
    from shutil import which
    ffplay = which("ffplay")
    if ffplay:
        subprocess.run([ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet",
                        "-volume", str(int(volume * 100)), str(path)],
                       capture_output=True)
    else:
        ps = ("Add-Type -AssemblyName presentationCore;"
              "$p=New-Object System.Windows.Media.MediaPlayer;"
              f"$p.Open([uri]'{path}');$p.Volume={volume};$p.Play();"
              "Start-Sleep -Seconds 3")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True)


def spoken_name(filename):
    """chatUserActionRequired.mp3 -> 'chat user action required'"""
    stem = Path(filename).stem
    out = []
    for ch in stem:
        if ch.isupper():
            out.append(" ")
        out.append(ch.lower())
    return "".join(out).replace("  ", " ").strip()


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    volume = float(cfg.get("volume", 0.5))

    mapped = {f for files in cfg.get("sounds", {}).values() for f in files}
    where = {}
    for event, files in cfg.get("sounds", {}).items():
        for f in files:
            where.setdefault(f, []).append(event)

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    files = sorted(p.name for p in SOUNDS.glob("*.mp3"))
    if mode == "mapped":
        files = [f for f in files if f in mapped]
    elif mode == "unmapped":
        files = [f for f in files if f not in mapped]

    print(f"{len(files)} sounds. Ctrl+C to stop.\n")
    for i, name in enumerate(files, 1):
        used = ", ".join(where.get(name, [])) or "not used"
        print(f"{i:2}/{len(files)}  {name:32} {used}")
        say(f"{spoken_name(name)}. {used.replace('-', ' ')}")
        play_blocking(SOUNDS / name, volume)
        time.sleep(0.6)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
