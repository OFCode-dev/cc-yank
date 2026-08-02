# cc-yank

**Copy Claude Code's answer with one keypress — as clean markdown, not as screen drawing.**

```
prefix + y   →   the whole answer, on your clipboard
```

Works over SSH, where there is no clipboard to write to.

---

## The problem

Claude Code is a TUI. It **renders** markdown to your terminal. A markdown table
becomes box-drawing characters padded to your terminal width; headings get a `●`
prefix; long lines wrap hard.

Select that with your mouse and you copy the *drawing*, not the *text*:

<table>
<tr><th>Mouse selection</th><th>cc-yank</th></tr>
<tr><td>

```
┌──────────────────┬───────────────┬────────
│      Katman      │ Tuş yakalar   │
│                  │      mı?      │
├──────────────────┼───────────────┼────────
│ Skill            │ Hayır         │ Skill…
```

</td><td>

```markdown
| Katman | Tuş yakalar mı? | Verdict |
|---|---|---|
| **Skill** | Hayır | Skill = model'e… |
```

</td></tr>
</table>

The left column pastes into GitHub, Jira or an editor as garbage. The right one
pastes correctly, because it is the source Claude actually wrote.

And the answer is interleaved with tool calls and their collapsed output, so
selecting only the prose means dragging precisely across a scrolling viewport.
You always grab too much or too little.

The clean text already exists in the session transcript. cc-yank reads it from
there and puts it on your clipboard.

## Install

```sh
git clone https://github.com/OFCode-dev/cc-yank ~/projects/cc-yank
cd ~/projects/cc-yank && ./install.sh
```

Then restart Claude Code **inside tmux**:

```sh
tmux new -s cc
claude
```

Ask something, and when the answer arrives press **`ctrl+b`** then **`y`**.
You should see `cc-yank: copied 2.4 KB [tmux/osc52]`.

Requirements: `python3` (stdlib only, no packages) and `tmux` for the keybinding.
Uninstall with `./install.sh --uninstall`.

## How it works

Two halves that never talk to each other — a file is the interface.

```
Claude finishes answering
  └─ Stop hook  →  tail the transcript backwards, keep the text blocks
                   →  ~/.local/state/cc-yank/pane-<n>.md

You press the key
  └─ tmux run-shell  →  read that file  →  clipboard
```

The hook does the work *before* you ask, so the keypress is instant. It never
involves the model, costs no tokens, and adds no latency to your session.

**What gets copied.** Every `text` block of the last answer, joined in order.
`thinking` and `tool_use` blocks are dropped. Tool results arrive as `user`
entries, so they are explicitly *not* treated as a turn boundary — an answer
split around three tool calls still comes out as one clean document.

**Transcripts get large** (100 MB+ on long sessions), so the file is read
backwards in chunks from EOF and stops at the last human message. Cost does not
grow with session length.

### OSC 52, or: why this works over SSH

On a remote box there is usually no clipboard at all — no X display, no
`xclip`. OSC 52 is a terminal escape sequence that asks the *terminal emulator*
— the one in front of you — to set its own clipboard. tmux emits it for us.

Backends, first match wins:

| Condition | Mechanism |
|---|---|
| inside tmux | `tmux load-buffer -w` (emits OSC 52) |
| macOS | `pbcopy` |
| Wayland | `wl-copy` |
| X11 | `xclip` / `xsel` |
| otherwise | raw OSC 52 to `/dev/tty` |

tmux needs `set-clipboard` set to `on` or `external` (`external` is the 3.x
default). Only `off` breaks it. The installer checks and reports.

Your terminal emulator must also honour OSC 52. Most do; a few require it to be
enabled. If the status line says `copied` but pasting gives you the old
clipboard, that is where to look.

## Notes and limits

- **tmux is required for the keybinding.** It is the only layer that can catch a
  keystroke while Claude Code's TUI is running. Without tmux, `bin/cc-yank`
  still works as a CLI.
- **Not a skill, and not `keybindings.json`.** A skill is an instruction to the
  model, and the model never sees your keystrokes. `keybindings.json` maps keys
  to a fixed list of built-in actions and cannot run a script — there is no
  "copy last response" action to bind.
- **Each tmux pane gets its own file**, so concurrent sessions never hand you
  another session's answer.
- **Payloads over 64 KB are truncated** on a line boundary, with a notice — OSC
  52 has an emulator-dependent size cap.
- **Nothing leaves your machine.** No network, no telemetry. The answer was
  already on your screen; cc-yank writes it to one local file.

## License

MIT
