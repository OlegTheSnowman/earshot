---
name: earshot
description: Manage Earshot, the audio and speech cues for Claude Code events — set volume, turn an event's sound or spoken announcement on or off, remap which sound plays for which event, audition sounds, adjust speech delays, or re-copy the sounds after a VS Code update. Use when the user mentions sounds, audio cues, signals, speech, announcements, volume, or says a sound is wrong, missing or annoying.
---

# Earshot

Audio cues for Claude Code events, using VS Code's accessibility signals.
Config is `config.json` in the plugin root. Sounds are in `sounds/`.

## Commands

Run these from the plugin root (`~/.claude/skills/vscode-signals`).

- Show the mapping: `python scripts/play.py --list`
- Audition one event: `python scripts/play.py <event> --test`
- Audition every sound with its name spoken first: `python scripts/audition.py`
- Re-copy sounds after a VS Code update: `python scripts/sync-sounds.py`

## Editing config.json

`volume` is 0.0 to 1.0. `enabled` turns everything off. `enabled_events` toggles
one event. `sounds` maps an event to a list of filenames in `sounds/`; several
filenames means one is picked at random, never the same twice in a row.

Event names here are one-per-sound, not one-per-hook. Where a Claude Code hook
supports matchers, each matcher value gets its own event name and its own entry
in `hooks/hooks.json` — `session-start-startup` and `session-start-fork` are two
events, not one event with logic behind it. `play.py` does no matching at all,
which is deliberate: the whole map is readable in one file.

Session: `session-start-startup`, `session-start-resume`, `session-start-clear`,
`session-start-compact`, `session-start-fork`, `session-end`.
Turn: `prompt-submit`, `stop`, `stop-failure`, `compact`.
Tools: `pre-read`, `pre-edit`, `pre-write`, `pre-bash`, `pre-glob`, `pre-grep`,
`pre-webfetch`, `pre-websearch`, `tool-done`, `tool-error`, `tool-batch`.
Attention: `permission-request`, `permission-denied`, `notification`.
Agents and tasks: `subagent-start`, `subagent-done`, `task-created`,
`task-completed`.

`session-end` and `compact` are disabled and have no hook wired; they are kept
so the mapping is easy to restore.

After editing, confirm it still parses and reads back right:

    python scripts/play.py --list

**Changes to `config.json` take effect immediately** — `play.py` re-reads it on
every sound. **Changes to `hooks/hooks.json` need a Claude Code restart**, because
hook files load once at session start. Wiring a new event and then testing it in
the same session proves nothing.

## Speaking the closing message

`speak_final_message` in `config.json` reads the message that ends each turn
through `accessible_output2`, which finds NVDA on its own. `speak_max_chars`
truncates at a sentence boundary and says so.

It hangs off `stop`, inside `play.py`, rather than having its own hook and script.
That is a deliberate trade: config and code changes then take effect immediately,
where a new entry in `hooks.json` would need a Claude Code restart before it could
be tested at all.

The text comes from the transcript, not from the hook payload - `Stop` carries
`transcript_path` and nothing else useful. `final_message` walks back from the end
of that file and stops at the first tool call, which is what separates the closing
message from the commentary between tool calls earlier in the same turn. Sidechain
rows are skipped, since a subagent's report is a working note rather than an answer.
Only the last 400KB is read, because a long session's transcript reaches megabytes
and this runs every turn.

`for_speech` exists because NVDA reads `` `tool-error` `` as "backtick tool dash
error backtick". Fenced blocks are announced by size rather than read out, em
dashes become commas, and link text survives while the URL doesn't.

`MessageDisplay` is the more obvious hook for this and was not used: it fires
while text is being displayed, so it arrives in pieces and would need buffering.
`Stop` gives one whole message.

**Speaking straight from the hook produces silence.** The NVDA controller reports
success, `fired.log` says the line was spoken, and nothing is heard - while the
identical call from a terminal speaks fine. Claude Code renders the finished
message a moment after `Stop`, NVDA cancels whatever it is saying to announce new
content, and our line dies in the queue. So the speech is spawned detached and
sleeps `speak_delay_ms` (1200 by default) before speaking. Raise it if a message
still gets cut off; the config is re-read every time, so it needs no restart.

Ruled out along the way, both by test rather than reasoning: message length (1055
characters spoke fine in one piece) and the interpreter or environment the hook
runs under (the same code spoke from the same Python when called directly).

## Things that were established the hard way

**Read the hook payload as bytes and decode UTF-8 yourself.** `sys.stdin.read()`
uses the locale encoding, which is `cp1251` here, and Claude Code writes UTF-8.
An em dash then arrives as three wrong characters and gets spoken as gibberish -
but the quiet one is worse: byte `0x98` is undefined in cp1251, and it is the
third byte of a left curly quote, which appears in ordinary prose constantly. The
decode raises, the `except` swallows it, and the script carries on with an *empty
payload*: "editing" with no filename, no `tool_use_id` so the sounds double up,
and at `stop` no transcript path, so nothing is spoken at all. Nothing in the
logs says anything went wrong.

`PostToolUse` fires only when a tool **succeeds**. Three deliberate failures (a
non-zero exit, a command-not-found, an Edit whose target string was absent)
produced no `PostToolUse` at all. Failure sounds come from `PostToolUseFailure`,
which is a real documented event despite appearing nowhere but one line of the
game-sounds plugin's config.

`SubagentStop` fires after **every ordinary turn**, not just after a real
subagent — which made the completion sound play twice on every response, two
seconds apart. A genuine subagent names itself in `agent_type`; the phantom one
leaves it empty. `play.py` drops both `subagent-start` and `subagent-done` when
that field is empty. Do not remove that guard without re-checking `fired.log`.

`tool-done` and `tool-batch` are for the tool calls that *didn't* announce
themselves. A Read plays its own sound before it runs, so the generic done sound
on top of it was two notes at once, three in a batch. When a `pre-*` sound plays,
`play.py` records that call's `tool_use_id`; the two after-sounds then stay quiet
for exactly those calls. `TodoWrite` still gets a done sound because nothing was
heard for it, and turning `pre-read` off in `config.json` brings the done sound
back for reads without touching `hooks.json` — no id is recorded, so nothing is
suppressed.

Matching on `tool_use_id` rather than tool name is not fussiness. **An Edit whose
`old_string` isn't in the file is rejected before `PreToolUse` fires**, and fires
no `PostToolUseFailure` either, so `PostToolBatch` is the only event that ever
sees it. Name-matching suppressed that too and made a failed edit completely
silent. Now an unannounced call whose `tool_response` carries `<tool_use_error>`
borrows `tool-error`'s sound, so a rejected edit is a `break`, not a `success`.

`PostToolBatch` fires after **every** tool, not just batches of several — that,
not `PostToolUse`, was the second sound playing on top of every read. It is the
last resort of the three: `tool-done` and `tool-error` record the call too, so a
tool with no `pre-` sound of its own gets one sound rather than two.

`TaskCreate` still makes two sounds, `task-created` and then `tool-done`. Its
`TaskCreated` payload has no `tool_use_id`, so the handshake above can't reach it.
Turn `task-created` off if the double bothers you.

**`PermissionDenied` does not fire when you press Deny.** It fires when the auto
mode classifier denies a call. A denial or an interrupt by the actual user fires
no hook at all — not `PostToolUseFailure`, not `PostToolBatch` — so it is silent
and nothing in this plugin can change that. The `permission-denied` event is
still wired, it just only ever speaks for auto mode.

Approving is handled, and needed handling: the tool plays its `pre-` sound
*before* the prompt, so approving it used to be followed by silence, with nothing
to say the approval had landed. `permission-request` now parks the tool name and
the following `tool-done` plays even though the call announced itself.
`PermissionRequest` carries no `tool_use_id`, hence matching on the name; one
slot is enough because a prompt blocks the turn.

`fired.log` in the plugin root records every sound that actually played, with a
timestamp, and every one deliberately skipped with the reason. It is the only way to tell a sound that is wired wrong from one that
is merely hard to tell apart by ear — and the first report here turned out to be
one of each. Delete it any time; it regrows.

## When a sound is wrong

The user judges these by ear and his reports are precise. "Too similar to the
other one" means two events need different files, not a volume change. Check
`fired.log` before theorising about which sound he actually heard. There are 31
files in `sounds/`; offer the unmapped ones first.
