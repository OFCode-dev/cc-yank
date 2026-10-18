# Changelog

## Unreleased

### Fixed
- Restore Linux CI by explicitly quoting empty `CDPATH` assignments.
- Preserve the order of multiple text blocks within an assistant message and
  skip malformed message fields without losing the rest of the answer.
- Retry missing or disappearing transcripts during settling, retaining text
  already read and falling back to the Stop payload at the deadline.
- Give overlapping hook writes separate atomic temporary files; store answers
  with owner-only permissions.
- Quote hook and tmux commands for repository paths containing shell syntax,
  including when upgrading existing installations.
- Keep repeated installs idempotent when `set-clipboard` is off; preserve
  settings permissions and unrelated hooks on install and uninstall.
- Remove tmux bindings even if tmux has been removed, avoid creating config
  during uninstall, and replace the `kill-server` advice with `unbind-key y`.

### Added
- Regression tests for extraction races and installer behavior in disposable
  homes, including a real tmux configuration/parser check on Linux CI.

## 0.2.0 — 2026-09-14

### Changed
- The hook no longer settles blind. It uses the Stop payload's
  `last_assistant_message` as a completion signal: poll the transcript until
  it ends with that text, re-parsing only when the file size changes. Typical
  hook cost drops from ~0.9 s to < 50 ms, and the last paragraph can no longer
  be lost — if the transcript is still behind after 3 s the payload text is
  appended. Older Claude Code without the field falls back to the quiet-period
  settle.
- `TMUX_PANE` is read from the hook's environment first (Claude Code 2.1.x
  passes it through); the `/proc` ancestor walk is now the fallback.
- Stored answers older than 30 days are pruned by the hook.

### Added
- `cc-yank --list`, `--path`, `--help` (inspection when the key says "nothing
  to copy yet"), `--clean`, `--version`.
- Test suite (`make test`: python3 unittest + POSIX sh), `make lint`
  (shellcheck), GitHub Actions on Linux and macOS.
- This changelog.

### Fixed
- The tmux binding broke when the repo path contained a space.
- Design notes corrected: hooks do inherit `TMUX_PANE` on current versions;
  the transcript lag is measured rather than assumed.

## 0.1.0 — 2026-08-02

Initial release: Stop hook extracts the last answer's markdown from the
transcript; `prefix + y` delivers it via tmux/OSC 52, pbcopy, wl-copy, xclip,
xsel or raw OSC 52.
