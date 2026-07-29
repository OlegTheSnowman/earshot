#!/usr/bin/env python3
"""
Play one VS Code accessibility signal for a Claude Code hook event.

    python play.py <event> [--list] [--test]

The sounds are VS Code's own accessibility signals, copied out of the install.
They are worth using rather than replacing: they were designed for people who
are listening to their editor rather than looking at it, so the five events
that matter most are already distinct from each other by intent.

Two rules this file exists to enforce.

It must never block. Hooks run synchronously and Claude Code waits for them to
exit, so a two-second sound played in the foreground is a two-second stall on
every prompt. The player is spawned detached and this process returns at once.

It must never fail. A hook that exits non-zero can interrupt the turn, and an
audio cue is not worth breaking a session over. Everything below is wrapped so
that any failure - missing file, missing player, malformed config - exits 0 in
silence. If sounds stop working, run with --test to see the error that is
otherwise swallowed.
"""

import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
SOUNDS = ROOT / "sounds"

# Remembers the last file played per event so a repeat can be avoided. In the
# temp dir because it is pure scratch - losing it costs one possible repeat.
STATE = Path(tempfile.gettempdir()) / "vscode-signals-last.json"

# Tool calls that already played a sound before they ran, so the generic
# after-sounds can stay quiet for them. See remember_announced.
ANNOUNCED = Path(tempfile.gettempdir()) / "vscode-signals-announced.json"

# The tool currently waiting on a permission decision. See
# take_pending_permission.
PENDING = Path(tempfile.gettempdir()) / "vscode-signals-pending.txt"

# A closing message that was ready but not spoken because Claude Code wasn't
# focused. Replay it with: python play.py --replay-last
UNSPOKEN = Path(tempfile.gettempdir()) / "vscode-signals-unspoken.txt"


def load_config():
    with open(CONFIG, encoding="utf-8") as fh:
        return json.load(fh)


def pick(event, cfg):
    """Choose a sound file for this event, avoiding an immediate repeat."""
    names = cfg.get("sounds", {}).get(event, [])
    files = [SOUNDS / n for n in names if (SOUNDS / n).exists()]
    if not files:
        return None
    if len(files) == 1:
        return files[0]

    last = {}
    try:
        last = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        pass

    choices = [f for f in files if f.name != last.get(event)] or files
    chosen = random.choice(choices)

    try:
        last[event] = chosen.name
        STATE.write_text(json.dumps(last), encoding="utf-8")
    except Exception:
        pass
    return chosen


def num(cfg, key, default):
    """
    A numeric setting, falling back on anything that isn't one. cfg.get only
    covers a missing key - a null or a typo'd string still reaches int() and
    throws, which would cost a whole announcement over a config typo.
    """
    try:
        return type(default)(cfg.get(key, default))
    except Exception:
        return default


_CACHED_CONFIG = None


def cached_config():
    """
    The config, read once per process. Logging decisions need it in places that
    don't otherwise have it - including the detached speech child, which never
    loads it at all.
    """
    global _CACHED_CONFIG
    if _CACHED_CONFIG is None:
        try:
            _CACHED_CONFIG = load_config()
        except Exception:
            _CACHED_CONFIG = {}
    return _CACHED_CONFIG


def log(message):
    """
    Append to fired.log when log_events is on - off by default, because it is a
    diagnostic for when something is wrong rather than something to accumulate
    on every machine that installs this. It is still the only way to tell a
    sound that is wired wrong from one that is merely hard to tell apart by ear,
    and it records deliberate silences with their reason, so turn it on before
    reporting anything odd. Delete the file any time; it regrows.
    """
    if not cached_config().get("log_events"):
        return
    try:
        import datetime
        with open(rotated(ROOT / "fired.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%H:%M:%S}  {message}\n")
    except Exception:
        pass


def rotated(path, limit=1_000_000):
    """
    The same path, having moved it aside if it got big. These logs grow at a
    few KB an hour and nothing ever trimmed them; the one you want to read when
    something is wrong is the one that would have become tens of megabytes.
    """
    try:
        if path.exists() and path.stat().st_size > limit:
            path.replace(path.with_suffix(path.suffix + ".1"))
    except Exception:
        pass
    return path


def remember_announced(tool_use_id):
    """
    Record that this exact tool call already made a sound before it ran.

    Keyed on tool_use_id, which PreToolUse, PostToolUse and every entry in a
    PostToolBatch all carry, so the after-sounds can tell "this call announced
    itself" from "a call to the same tool announced itself a moment ago". An
    earlier version matched on tool name and got that wrong in the one case
    that matters: an Edit whose old_string is absent is rejected before
    PreToolUse fires, so it makes no sound at all, and name-matching then
    swallowed the batch sound too and left the failure completely silent.

    The list is capped and kept in the temp dir - it is pure scratch, and
    losing it costs one redundant sound.
    """
    if not tool_use_id:
        return
    ids = read_announced()
    ids.append(tool_use_id)
    try:
        # Written to a sibling and renamed over the top, because parallel tool
        # calls run these hooks concurrently: a plain write truncates first, and
        # a reader landing in that window sees an empty file, concludes nothing
        # was announced, and plays a full extra round of sounds.
        handle, temp = tempfile.mkstemp(dir=str(ANNOUNCED.parent),
                                        prefix="vscode-signals-ann-")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(ids[-64:], fh)
        os.replace(temp, ANNOUNCED)
    except Exception:
        pass


def read_announced():
    try:
        ids = json.loads(ANNOUNCED.read_text(encoding="utf-8"))
        return ids if isinstance(ids, list) else []
    except Exception:
        return []


def batch_ids(payload):
    """
    The tool_use_ids in a PostToolBatch payload, or None if the shape isn't one
    we recognise. None means "play the sound" - a batch nobody can read is
    better announced than silently dropped.
    """
    calls = payload.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    ids = []
    for item in calls:
        if not isinstance(item, dict) or not item.get("tool_use_id"):
            return None
        ids.append(item["tool_use_id"])
    return ids


def final_message(transcript_path):
    """
    The text of the message that just ended the turn, and nothing else.

    Walks back from the end of the transcript collecting assistant text, and
    stops at the first tool call - which is what separates the closing message
    from the running commentary between tool calls earlier in the same turn.
    Sidechain rows are subagent output and are skipped: a subagent's report is
    a working note, not something anyone wants read out.

    Only the tail of the file is read. A long session's transcript reaches
    megabytes and this runs on every turn.
    """
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 400_000))
            raw = fh.read().decode("utf-8", "replace")
    except Exception:
        return ""

    lines = raw.splitlines()
    if size > 400_000 and lines:
        lines = lines[1:]  # first line is probably cut in half

    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass

    out = []
    for row in reversed(rows):
        # Every shape below has been seen in a real transcript, and getting any
        # of them wrong throws - after the stop sound has already played, so it
        # looks exactly like the screen reader swallowing the message rather
        # than like a crash.
        if not isinstance(row, dict) or row.get("isSidechain"):
            continue
        kind = row.get("type")
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            content = [] if kind != "user" else content

        # Stop at the start of this turn. A turn that made no tool calls at all
        # has nothing else to stop it, so without this the walk ran back
        # through the user's message and swallowed the tail of the previous
        # answer - which is what it sounded like: half of the last message with
        # this one stuck on the end.
        if kind == "user" and not is_tool_result(content):
            break
        if kind != "assistant" or not isinstance(content, list):
            continue
        if any(isinstance(b, dict) and b.get("type") == "tool_use"
               for b in content):
            break
        for block in reversed(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text", "")).strip():
                out.append(block["text"])
    return "\n".join(reversed(out)).strip()


def is_tool_result(content):
    """
    Whether a user row is a tool result rather than something the person typed.
    A typed message arrives as a plain string; a tool result is a list of
    blocks. Only the typed one ends the turn.
    """
    if isinstance(content, str):
        return False
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_result"
                   for b in content)
    return False


def for_speech(text, max_chars):
    """
    Markdown as a screen reader should hear it.

    Left alone, NVDA reads `tool-error` as "backtick tool dash error backtick"
    and a fenced block line by line including the fence. Code is summarised
    rather than read: the length is the useful part, the contents are not
    something anyone wants spoken.
    """
    import re

    def fence(match):
        lines = match.group(2).strip("\n").count("\n") + 1
        lang = (match.group(1) or "").strip()
        return f" ({lang} code block, {lines} lines) " if lang else \
               f" (code block, {lines} lines) "

    text = re.sub(r"```(\w*)\n(.*?)```", fence, text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)              # inline code
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links keep the words
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", text)
    text = re.sub(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", "", text, flags=re.M)
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r"\s*→\s*", " to ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if max_chars and len(text) > max_chars:
        cut = text.rfind(". ", 0, max_chars)
        text = text[:cut + 1] if cut > max_chars // 2 else text[:max_chars]
        text += " Message truncated."
    return text


def foreground_exe():
    """
    The executable owning the focused window, lowercased, or "" if it can't be
    told. Windows only, through ctypes so it needs nothing installed.
    """
    if os.name != "nt":
        return ""
    try:
        import ctypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # 0x1000 is PROCESS_QUERY_LIMITED_INFORMATION - enough for the name,
        # and unlike PROCESS_QUERY_INFORMATION it works without elevation.
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                ctypes.byref(size))
            return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def listening(cfg):
    """
    Whether the person is actually looking at Claude Code right now.

    Speech that follows you into another window is worse than no speech: it
    talks over whatever you are reading there, and NVDA is already busy with
    that window's own output. Sounds are unaffected - a tone from a background
    window is information, a sentence is an interruption.
    """
    if not cfg.get("speak_only_when_focused", True):
        return True
    names = [n.lower() for n in cfg.get("focus_process_names", [])]
    exe = foreground_exe()
    if not exe:
        return True  # can't tell - better to speak than to go silent
    return exe in names


def phrase_for(event, payload):
    """
    What to say for a tool that is about to run.

    Bash gets the description written for the call - which is already a plain
    sentence about intent, "inspecting accessibility properties" rather than
    "running a command", and is more use than the command line would be.
    Everything else is built from the one field that says what it is acting on,
    and paths are cut to the filename because hearing a full Windows path read
    out is unbearable.
    """
    tool_input = payload.get("tool_input") or {}

    def name(key):
        value = tool_input.get(key) or ""
        return os.path.basename(str(value).rstrip("/\\")) or str(value)

    if event == "pre-bash":
        return tool_input.get("description") or "running a command"
    if event == "pre-read":
        return f"reading {name('file_path')}"
    if event == "pre-edit":
        return f"editing {name('file_path')}"
    if event == "pre-write":
        return f"writing {name('file_path')}"
    if event == "pre-glob":
        return f"finding files matching {tool_input.get('pattern', '')}"
    if event == "pre-grep":
        return f"searching for {tool_input.get('pattern', '')}"
    if event == "pre-websearch":
        return f"searching the web for {tool_input.get('query', '')}"
    if event == "pre-webfetch":
        host = str(tool_input.get("url", "")).split("/")[2:3]
        return f"fetching {host[0] if host else 'a page'}"
    return ""


def permission_phrase(payload):
    """
    What to say when something is waiting on an answer.

    Both a permission prompt and an AskUserQuestion arrive as PermissionRequest,
    which is lucky: that hook was already wired, so this needed no new entry in
    hooks.json and no restart to try.

    A question is read with its options numbered, because the answer is chosen
    from a list and "yes spoken fully, only NVDA's own reading, nothing at all"
    run together is not a list anyone can hold in their head. A permission
    prompt is read as what it wants to do, using the same phrasing as the tool
    announcements.
    """
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool == "AskUserQuestion":
        parts = []
        questions = tool_input.get("questions")
        for question in questions if isinstance(questions, list) else []:
            if not isinstance(question, dict):
                continue
            parts.append(str(question.get("question", "")).strip())
            options = question.get("options")
            if isinstance(options, list) and options:
                listed = ". ".join(
                    f"{i}. {o.get('label', '') if isinstance(o, dict) else o}"
                    for i, o in enumerate(options, 1))
                parts.append(f"Options: {listed}.")
        return " ".join(p for p in parts if p) or "A question is waiting."

    if tool in ("Bash", "PowerShell"):
        what = tool_input.get("description") or tool_input.get("command", "")
        return f"Permission needed to run: {what}"

    if tool in ("Read", "Edit", "Write") and "file_path" in tool_input:
        return (f"Permission needed to {tool.lower()} "
                f"{os.path.basename(str(tool_input['file_path']))}")
    return f"Permission needed for {tool}."


def speak(text, interrupt=False):
    """
    Hand the text to whatever screen reader is running. Imported here rather
    than at the top because it is only needed on one event and costs real time
    to load, and a hook must not be slow.

    Tool announcements interrupt: several tools can run in a second, and a
    queue of stale "editing config.json" lines arriving after the fact is worse
    than only ever hearing the current one. The closing message does not, so it
    doesn't cut off the last tool it just announced.
    """
    try:
        import accessible_output2.outputs.auto as auto
        out = auto.Auto()
        name = type(out.get_first_available_output()).__name__
        out.speak(text, interrupt=interrupt)
        return name
    except Exception as exc:
        return f"FAILED {exc}"


def speak_detached(text, delay_ms, interrupt=False):
    """
    Speak from a separate process, after a pause.

    Speaking straight from the hook reached NVDA and produced silence: the
    controller reported success, and the same call from a terminal spoke fine.
    What differs at Stop is that Claude Code renders the finished message a
    moment later, and NVDA cancels what it is saying to announce new content -
    so our line is queued and then thrown away. Waiting until the screen has
    settled is the only way round it from outside NVDA.

    Detached because the wait must not be charged to the hook, which Claude
    Code blocks on. The text goes through a file rather than the command line
    to keep quoting out of it entirely.
    """
    try:
        handle, name = tempfile.mkstemp(suffix=".txt", prefix="vscode-signals-")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--speak-file", name, "--delay-ms", str(delay_ms)]
        if interrupt:
            cmd.append("--interrupt")
        try:
            spawn(cmd)
        except Exception:
            # The only unlink lives in the child, so a spawn that never
            # happened strands the file in %TEMP% for good.
            os.unlink(name)
            raise
        return "detached"
    except Exception as exc:
        return f"FAILED {exc}"


def speak_from_file(path, delay_ms, interrupt=False):
    import time

    time.sleep(max(0, delay_ms) / 1000.0)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        text = ""
    result = speak(text, interrupt=interrupt) if text else "empty"

    # The full text, verbatim, because "what did it actually say" is not a
    # question fired.log can answer and mishearing it is easy. Off by default
    # like fired.log, and more sensitive than it: this file is a transcript of
    # everything said to you, so it is not something to leave on, or to commit.
    if not cached_config().get("log_spoken"):
        return
    try:
        import datetime
        with open(rotated(ROOT / "spoken.log"), "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {datetime.datetime.now():%H:%M:%S} "
                     f"via {result}, {len(text)} chars =====\n{text}\n")
    except Exception:
        pass
    try:
        os.unlink(path)
    except Exception:
        pass


def take_pending_permission(tool_name):
    """
    True if this tool is the one that just asked for permission, clearing the
    flag as it goes.

    A tool that prompts has already played its pre- sound, so approving it
    otherwise led to silence - the prompt sound, then nothing to say the
    approval had landed and the tool had run. The completion sound is worth
    hearing in that one case even though the tool announced itself.

    A single slot is enough because a permission prompt blocks the turn: there
    is never more than one outstanding. PermissionRequest carries no
    tool_use_id, which is why this matches on the name.
    """
    try:
        stored = json.loads(PENDING.read_text(encoding="utf-8"))
        pending, when = stored.get("tool"), float(stored.get("ts", 0))
    except Exception:
        return False
    if not pending:
        return False

    # Only the tool that actually asked may consume the slot. Clearing it first
    # meant any unrelated tool-done ate the flag, and the approved tool then
    # fell through to the "announced" branch and finished in silence - the very
    # thing this exists to prevent. A stale entry is dropped on age, because a
    # denial leaves the slot set and nothing else ever clears it.
    import time

    stale = (time.time() - when) > 120
    if pending == tool_name or stale:
        try:
            PENDING.unlink()
        except Exception:
            pass
    return pending == tool_name and not stale


def failed(call):
    """
    Whether one entry of a PostToolBatch failed. The response is a plain string
    for the tools that fail this way, and a rejected call carries the same
    <tool_use_error> marker the model is shown.
    """
    response = call.get("tool_response")
    if isinstance(response, str):
        return "<tool_use_error>" in response
    if isinstance(response, dict):
        return bool(response.get("is_error"))
    # A response can also be a list of content blocks. Returning False there
    # meant the one case the caller cares about - an unannounced failure in a
    # batch - got the success sound.
    try:
        return "<tool_use_error>" in json.dumps(response)
    except Exception:
        return False


def spawn(cmd):
    """Start the player without a console window and without waiting for it."""
    flags = 0
    if os.name == "nt":
        # DETACHED_PROCESS so the player outlives this script, CREATE_NO_WINDOW
        # so no console flashes on screen for every sound.
        flags = 0x00000008 | 0x08000000
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )


def play(path, volume):
    """
    Play an mp3. ffplay first because it handles volume properly; PowerShell's
    MediaPlayer as the fallback because it is always present on Windows and
    needs no install. The game-sounds plugin has neither branch, which is why
    its sounds only work here at all when ffmpeg happens to be on PATH.
    """
    from shutil import which

    ffplay = which("ffplay")
    if ffplay:
        spawn([ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet",
               "-volume", str(int(volume * 100)), str(path)])
        return "ffplay"

    if os.name == "nt":
        ps = (
            "Add-Type -AssemblyName presentationCore;"
            "$p=New-Object System.Windows.Media.MediaPlayer;"
            # Doubling the quote is what keeps a path like O'Brien from ending
            # the PowerShell string early and failing silently.
            f"$p.Open([uri]'{str(path).replace(chr(39), chr(39) * 2)}');"
            f"$p.Volume={volume};$p.Play();"
            "Start-Sleep -Seconds 3"
        )
        spawn(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
        return "powershell"

    for player in ("afplay", "paplay", "pw-play"):
        if which(player):
            spawn([player, str(path)])
            return player
    return None


def flag(name, default=None):
    if name in sys.argv:
        index = sys.argv.index(name)
        if index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return default


def main():
    # The detached half of speak_detached re-enters this file.
    if "--replay-last" in sys.argv:
        try:
            held = UNSPOKEN.read_text(encoding="utf-8")
        except Exception:
            held = ""
        speak(held or "Nothing held.", interrupt=True)
        return

    speak_file = flag("--speak-file")
    if speak_file:
        speak_from_file(speak_file, int(flag("--delay-ms", "0")),
                        "--interrupt" in sys.argv)
        return

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    test = "--test" in sys.argv
    event = args[0] if args else ""

    if "--list" in sys.argv:
        cfg = load_config()
        for name, files in cfg.get("sounds", {}).items():
            state = "on " if cfg.get("enabled_events", {}).get(name) else "off"
            print(f"{state} {name:16} {', '.join(files)}")
        return

    if not event:
        return

    payload = {}
    try:
        if not sys.stdin.isatty():
            # Read bytes and decode as UTF-8 explicitly. sys.stdin.read() uses
            # the locale encoding, which is cp1251 on this machine, and Claude
            # Code writes the payload as UTF-8. That combination fails two ways:
            # an em dash arrives as three wrong characters and gets spoken as
            # gibberish, and a curly quote - which appears in ordinary prose all
            # the time - is an undefined byte in cp1251 and raises, leaving the
            # payload empty. An empty payload is the quiet one: the filename
            # vanishes from "editing", tool_use_id goes missing so sounds double
            # up, and at stop the transcript path is lost and nothing is spoken
            # at all.
            stream = getattr(sys.stdin, "buffer", None)
            raw = (stream.read().decode("utf-8", "replace") if stream
                   else sys.stdin.read())
            if raw.strip():
                payload = json.loads(raw)
    except Exception:
        pass

    # SubagentStop fires after every ordinary turn in this build, not just
    # after a real subagent, so the success sound played twice on every
    # response - once for Stop, once two seconds later for this. A genuine
    # subagent names itself in agent_type; the phantom one leaves it empty.
    if event in ("subagent-start", "subagent-done") and not payload.get("agent_type"):
        return

    cfg = load_config()
    if not cfg.get("enabled", True):
        return
    if not cfg.get("enabled_events", {}).get(event, False):
        if test:
            print(f"{event} is disabled in config.json")
        return

    # A tool call that announced itself before it ran doesn't need a second
    # sound after it: reading a file played two notes on top of each other, and
    # in a batch a third. The generic done and batch sounds are for the calls
    # nothing was heard for - which includes a call that was rejected before
    # PreToolUse could fire. Failures are exempt: tool-error is never redundant,
    # because a tool that fails produced no other sound saying so.
    if event == "permission-request":
        try:
            import time
            PENDING.write_text(
                json.dumps({"tool": payload.get("tool_name", ""),
                            "ts": time.time()}), encoding="utf-8")
        except Exception:
            pass
    elif event == "permission-denied":
        try:
            PENDING.unlink()
        except Exception:
            pass

    if event == "tool-done" and not test:
        if take_pending_permission(payload.get("tool_name")):
            log(f"{event} kept, {payload.get('tool_name')} needed permission")
        elif payload.get("tool_use_id") in read_announced():
            log(f"{event} skipped, {payload.get('tool_name')} announced itself")
            return

    if event == "tool-batch" and not test:
        ids = batch_ids(payload)
        if ids is None:
            log(f"{event} payload not understood: {sorted(payload)}")
        else:
            announced = read_announced()
            if all(i in announced for i in ids):
                log(f"{event} skipped, all {len(ids)} call(s) announced")
                return
            # An Edit rejected for a missing old_string never reaches
            # PreToolUse or PostToolUseFailure, so this is the only event that
            # can report it - and reporting a failure with the success sound is
            # worse than not reporting it. Borrow tool-error's sound instead.
            silent = [c for c in payload["tool_calls"]
                      if c.get("tool_use_id") not in announced]
            if silent and any(failed(c) for c in silent):
                event = "tool-error"
                log("tool-batch: unannounced failure, using the error sound")

    path = pick(event, cfg)
    if path is None:
        if test:
            print(f"no sound file found for {event}")
        return

    # Guarded because everything below it matters more than the sound does. A
    # player that throws used to unwind the whole function, so a broken ffplay
    # took the spoken message with it - exactly the wrong priority.
    try:
        used = play(path, num(cfg, "volume", 0.5))
    except Exception as exc:
        used = None
        log(f"{event} sound failed: {exc}")
    if test:
        print(f"{event} -> {path.name} via {used or 'NO PLAYER FOUND'}")

    # Any sound that named this call counts, not just the ones before it ran:
    # a tool with no pre- sound of its own got tool-done AND tool-batch, which
    # is the same doubling one layer down. tool-batch is the last resort, for
    # the calls nothing else spoke for.
    if event.startswith("pre-") or event in ("tool-done", "tool-error"):
        remember_announced(payload.get("tool_use_id"))

    log(f"{event:16} {path.name}")

    # Read the closing message aloud. Folded in here rather than given its own
    # hook so it needs no restart to change - see SKILL.md.
    if event == "stop" and cfg.get("speak_final_message") and not test:
        text = final_message(payload.get("transcript_path", ""))
        if text and listening(cfg):
            spoken = for_speech(text, num(cfg, "speak_max_chars", 0))
            delay = num(cfg, "speak_delay_ms", 1200)
            log(f"speak {speak_detached(spoken, delay)}, "
                f"{len(spoken)} chars, {delay}ms")
        elif text:
            # Held rather than dropped. A tool announcement is worth losing when
            # you are in another window; the closing message is the entire point
            # of the turn, and the long turns you step away from are exactly the
            # ones worth hearing. Single slot, overwritten, replayed with
            # --replay-last.
            try:
                UNSPOKEN.write_text(
                    for_speech(text, num(cfg, "speak_max_chars", 0)),
                    encoding="utf-8")
            except Exception:
                pass
            log(f"speak held, focus is {foreground_exe() or 'unknown'}")

    if event == "permission-request" and cfg.get("speak_prompts") and not test:
        phrase = permission_phrase(payload)
        if not phrase:
            log("asked nothing, no phrase built")
        elif not listening(cfg):
            log(f"ask skipped, focus is {foreground_exe() or 'unknown'}")
        else:
            speak_detached(phrase, num(cfg, "speak_prompt_delay_ms", 900),
                           interrupt=True)
            log(f"asked {phrase[:70]!r}")

    if event.startswith("pre-") and cfg.get("speak_tools") and not test:
        phrase = phrase_for(event, payload)
        if phrase and not listening(cfg):
            log(f"say skipped, focus is {foreground_exe() or 'unknown'}")
        elif phrase:
            speak_detached(phrase, num(cfg, "speak_tool_delay_ms", 350),
                           interrupt=True)
            log(f"said {phrase!r}")


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        # BaseException, not Exception: the contract is that this never exits
        # non-zero, and a KeyboardInterrupt landing mid-hook would break it.
        # Silent by default; --test surfaces it.
        if "--test" in sys.argv:
            print(f"error: {exc}", file=sys.stderr)
    sys.exit(0)
