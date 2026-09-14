# Changelog

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
