# Design notes

Why cc-yank is shaped the way it is, and what the terminal actually does when
you try to copy from Claude Code. Most of this was measured rather than
assumed; where a first guess turned out wrong, the wrong guess is kept so the
reasoning is reconstructible.

## The three layers that could catch a keystroke

Only one of them can.

### Skills — cannot

A skill is a markdown file of instructions for the model. The model runs only
when you send a message; it never observes keystrokes, and it has no clipboard.
A skill saying "when the user presses ctrl+y, copy the answer" would be an
instruction nobody can execute.

### `keybindings.json` — cannot

Claude Code does have a real keybinding file, `~/.claude/keybindings.json`:

```json
{ "context": "Chat", "bindings": { "ctrl+e": "chat:externalEditor" } }
```

The right-hand side must be one of a fixed set of built-in actions —
`chat:submit`, `app:toggleTranscript`, `scroll:pageUp`, and so on. It cannot
run a script, and the action list contains nothing that copies an answer.
`selection:copy` exists but means "copy the text you manually selected", which
is the behaviour we are trying to replace.

### Stop hook + tmux — works

A `Stop` hook runs when the model finishes a turn. tmux catches keys while the
TUI is running. Neither half knows about the other; a file is the interface.

```
Claude finishes answering
  └─ Stop hook → tail transcript → ~/.local/state/cc-yank/pane-<n>.md

You press prefix + y
  └─ tmux run-shell → read that file → clipboard
```

The hook does the work before you ask, so the keypress is instant.

## Why not scrape the scrollback

Because the scrollback is the problem. Claude Code renders markdown: a table
becomes box-drawing characters padded to terminal width, headings get a `●`
prefix, long lines wrap hard. Copying the pane reproduces exactly the
corruption we are trying to avoid. The transcript holds the model's original
markdown; that is the only clean source.

## Transcript format

One JSON object per line. Measured shape:

```jsonc
{"type":"assistant","isSidechain":false,"message":{"content":[{"type":"text","text":"…"}]}}
{"type":"assistant","isSidechain":false,"message":{"content":[{"type":"tool_use","name":"Bash"}]}}
{"type":"user","message":{"content":[{"type":"tool_result","content":"…"}]}}
{"type":"user","message":{"content":"a real question"}}
{"type":"attachment"} {"type":"mode"} {"type":"system"} {"type":"ai-title"} …
```

Four things this dictates:

1. **One content block per assistant line.** An answer is spread across many
   lines, so all of them must be collected and re-joined in order.
2. **Tool results are `user` entries.** Naively stopping at the first `user`
   entry truncates every answer that used a tool. The boundary test is "a
   `user` entry containing at least one non-`tool_result` block".
3. **`isSidechain` marks subagent traffic**, which is not what you just read.
4. **A dozen metadata types** (`attachment`, `mode`, `system`, `last-prompt`,
   `file-history-*`, …) are interleaved and must be skipped.

Transcripts reach 100 MB+, so the file is read backwards in fixed-size chunks
from EOF and stops at the last human message. Verified identical output at
chunk sizes of 256 KB, 64 B and 7 B — the last forces a split inside nearly
every line, which is where a backwards reader normally breaks.

## OSC 52

On a remote machine there is typically no clipboard at all: no `DISPLAY`, no
`xclip`. OSC 52 is a terminal escape sequence meaning "put this in *your*
clipboard", interpreted by the emulator at the far end of the SSH connection.
Without it, cc-yank is dead for most Claude Code users.

tmux emits it via `load-buffer -w`, subject to two conditions:

- **`set-clipboard`** must not be `off`. The man page is explicit that both
  `on` and `external` attempt to set the terminal clipboard; `external` merely
  refuses to let applications write tmux buffers. An early draft of this design
  said the installer should force `on` — wrong, and it would have downgraded
  the safer default.
- **terminfo needs an `Ms` entry.** `xterm-256color` ships
  `Ms=\E]52;%p1%s;%p2%s\007`. Without it tmux emits nothing at all, silently.

Payloads are capped by the emulator (xterm ~74 KB, several stricter). cc-yank
truncates at 64 KB *on a line boundary* — a raw byte cut splits a UTF-8
character and hands the terminal an invalid payload — and appends a notice
rather than silently delivering a broken paste.

## Keying: which file belongs to which session

This took three wrong answers.

**`TMUX_PANE` from the hook's own environment.** Claude Code spawns hooks with
a sanitised environment, so `TMUX_PANE` is absent even when the session is
inside tmux. The hook silently wrote a shared file.

**The controlling tty.** Attractive, because both sides can compute it: tmux
exposes `#{pane_tty}`, and a process can read its own from `/proc/self/stat`.
Except the hook has no controlling terminal — the field reads `0`. Dead end.

**A shared `default.md` fallback.** The obvious patch, and the worst one. With
several Claude Code sessions running, every session that fails to resolve its
pane writes to the same file; the binding then hands you *another session's
answer*. Delivering nothing is strictly better than delivering the wrong thing,
so this fallback was removed rather than fixed.

**What works:** walk up `/proc/<pid>/environ` from the hook to an ancestor that
still carries `TMUX_PANE`. Linux-only; elsewhere the hook writes `default.md`
and the keybinding reports "nothing to copy yet" rather than guessing.

## The Stop-hook race

`Stop` fires before Claude Code has flushed the turn's final text block to the
transcript. Reading immediately loses the last paragraph — reliably, not
intermittently.

The hook therefore settles first: sleep, then re-read until two consecutive
extractions agree (~0.8 s typical, 2.2 s worst case). It runs in the background
long before the key is pressed, so the delay is invisible.

## Deliberate non-goals

- **No model involvement.** The hook is plain Python over a file. It costs no
  tokens and adds no latency to a session.
- **No network, no telemetry.** The answer was already on your screen; it is
  written to one local file.
- **No raw keybinding by default.** `bind-key -n` would swallow the key before
  Claude Code's TUI sees it. Prefix-based bindings cannot collide.
