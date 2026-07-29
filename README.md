# Earshot

Earshot tells you what Claude Code is doing without you having to look. Every
event gets its own short sound, and the things worth words get read out through
your screen reader: the message at the end of a turn, each tool as it runs, and
any permission prompt or question waiting on you.

I'm blind, and I built this because I was losing track of what Claude was up to
between tool calls. If you use a screen reader and Claude Code, it's for you. If
you don't, the sounds alone are still useful and you can leave the speech off.

## What you get

**A distinct sound per event.** Reads, edits, writes, shell commands, searches,
web fetches, permission prompts, failures, subagents, the end of a turn. Only one
sound per tool call, never a pile of them.

**The closing message read aloud.** When a turn ends, the final message is spoken.
Only that message, not the running commentary between tool calls, and not
subagent output or tool results. Code blocks are announced by size instead of
read out, because nobody wants a fenced block spelled at them.

**Tool announcements.** "reading About.xml", "editing config.json", "fetching
example.com". Shell commands announce their intent rather than their command line,
so you hear "checking which announcements went out" instead of "running a bash
command".

**Questions and permission prompts spoken.** A prompt used to be a tone and
nothing else, so you had to go and read it before you could answer. Now you hear
"Permission needed to run: deleting the build folder", or the question with its
options numbered.

**Failures say what broke.** "Edit failed on config.json. String to replace not
found in file." A sound tells you something went wrong; it doesn't tell you what,
and going to look is the thing this is meant to save you.

**Task events say which task.** "Task created: fix the parser." Not just a chime
that something happened to something.

**It stops talking when you leave.** Speech only happens while Claude Code is the
focused window. A tone from a background window is information; a sentence talking
over what you're reading somewhere else is not. A closing message you missed
because you tabbed away is held rather than thrown away — `python
scripts/play.py --replay-last` says it.

## Installing

```
/plugin marketplace add OlegTheSnowman/earshot
/plugin install earshot
```

Restart Claude Code after installing. Hook files load once at session start, so a
plugin that adds hooks does nothing until you restart.

For speech, install `accessible_output2`:

```
pip install accessible_output2
```

That's the good path, because it talks to NVDA directly and uses your voice, your
rate and your interrupt behaviour. Without it you still get every sound, and
speech falls back to the SAPI voice that ships with Windows.

Speech and the focus check are Windows-only right now. Sounds work anywhere that
has `ffplay` or a system player. If you're on macOS or Linux and want this kind of
thing today, [claude-sonar](https://github.com/vylasaven/claude-sonar) covers those
platforms and has done for longer than Earshot has existed.

## What it reads and runs

Worth knowing before you install anything that hooks every event.

To speak the closing message, Earshot reads the tail of your session transcript.
Claude Code hands it the path in the `Stop` payload, and there is nowhere else the
message exists. It reads the last 400KB, takes the closing text, and keeps
nothing.

Nothing is sent anywhere. There is no network access in any of the scripts. The
only files written are the two logs, both off by default, and a short-lived temp
file holding the sentence being spoken, which the speaking process deletes.

It runs `ffplay` if you have it, falls back to PowerShell's media player, and
spawns a detached copy of `play.py` to speak. That's all the process creation
there is.

`spoken.log` is the one to be careful with. When enabled it records every message
read aloud to you, verbatim. It's off by default and gitignored.

## Settings

Everything lives in `config.json` next to this file, and it's re-read on every
sound, so changes take effect immediately. You don't need to restart Claude Code,
which you do for anything you change in `hooks/hooks.json`.

| Setting | Default | What it does |
| --- | --- | --- |
| `volume` | `0.75` | Sound volume, 0 to 1. |
| `enabled` | `true` | All sounds off when false. |
| `speak_final_message` | `true` | Read the message that ends a turn. |
| `speak_tools` | `true` | Announce each tool as it runs. |
| `speak_prompts` | `true` | Read permission prompts and questions. |
| `speak_failures` | `true` | Say which tool failed and why. |
| `speak_tasks` | `true` | Say which task was created or completed. |
| `speak_only_when_focused` | `true` | Stay quiet when Claude Code isn't the focused window. |
| `focus_process_names` | `claude.exe`, `code.exe`, `windowsterminal.exe` | Which windows count as focused. |
| `speak_max_chars` | `0` | Truncate long messages. 0 means never. |
| `speak_delay_ms` | `1200` | Wait before speaking the closing message. |
| `speak_tool_delay_ms` | `350` | Wait before a tool announcement. |
| `speak_prompt_delay_ms` | `900` | Wait before a prompt is read. |
| `log_events` | `false` | Write `fired.log`, for when something is wrong. |
| `log_spoken` | `false` | Write `spoken.log`, a verbatim record of everything said. |
| `enabled_events` | all on | Turn a single event's sound off. |
| `sounds` | see file | Which file plays for which event. Several filenames means one at random. |

The delays exist for a real reason. Speaking the instant a hook fires reaches the
screen reader and produces silence: NVDA cancels what it's saying to announce the
newly rendered content, and your line dies in the queue. Waiting for the screen to
settle is the only fix from outside NVDA. If a message still gets cut off, raise
the delay.

## Commands

Run these from the plugin directory.

```
python scripts/play.py --list          # every event and its sound
python scripts/play.py stop --test     # play one event's sound
python scripts/audition.py             # play every sound, name spoken first
python scripts/play.py --replay-last   # say the message you missed
python scripts/sync-sounds.py          # re-copy sounds after a VS Code update
```

## When something's wrong

Turn on `log_events` in `config.json`, reproduce it, then read `fired.log`. It
records every sound that played, and every one deliberately skipped with the
reason — so a silence that was on purpose doesn't look like a broken hook. Turn on
`log_spoken` too if the problem is what was said rather than whether anything was.
Both are off by default and both are gitignored; `spoken.log` in particular is a
transcript of everything read to you.

## The sounds

They're Visual Studio Code's own accessibility signals, from the `microsoft/vscode`
repository, redistributed under its MIT licence. See
[sounds/LICENSE-sounds.txt](sounds/LICENSE-sounds.txt). I used them rather than
making my own because they were designed for people listening to an editor instead
of looking at one, so the events that matter are already distinct by intent.

Earshot isn't affiliated with Microsoft or with Anthropic.

## Licence

MIT. See [LICENSE](LICENSE).
