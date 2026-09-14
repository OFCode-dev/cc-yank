#!/bin/sh
# cc-yank installer — wires the Stop hook and the tmux keybinding.
#
#   ./install.sh              install
#   ./install.sh --uninstall  remove
#
# Idempotent: running it twice changes nothing the second time.

set -eu

REPO=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SETTINGS="$HOME/.claude/settings.json"
TMUX_CONF="$HOME/.tmux.conf"
MARK_BEGIN="# >>> cc-yank >>>"
MARK_END="# <<< cc-yank <<<"

uninstall=0
[ "${1:-}" = "--uninstall" ] && uninstall=1

say() { printf '  %s\n' "$1"; }

# ---------------------------------------------------------------- prereqs
command -v python3 >/dev/null 2>&1 || {
    echo "cc-yank: python3 is required (stdlib only, no packages)." >&2
    exit 1
}
chmod +x "$REPO/hooks/extract.py" "$REPO/bin/cc-yank"

# ---------------------------------------------------- ~/.claude/settings.json
# Merged with python3 rather than sed: this file holds the user's whole
# configuration and a botched edit costs them more than this tool is worth.
echo "settings.json"
if [ ! -f "$SETTINGS" ]; then
    mkdir -p "$(dirname "$SETTINGS")"
    printf '{}\n' > "$SETTINGS"
    say "created $SETTINGS"
fi

python3 - "$SETTINGS" "$REPO/hooks/extract.py" "$uninstall" <<'PY'
import json, os, shutil, sys, time

path, hook, uninstall = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
command = '"%s"' % hook

with open(path) as fh:
    settings = json.load(fh)

stop = settings.setdefault("hooks", {}).setdefault("Stop", [])
present = any(
    h.get("command") in (command, hook)
    for entry in stop for h in entry.get("hooks", [])
)

if uninstall:
    if not present:
        print("  no cc-yank hook registered")
        sys.exit(0)
    for entry in stop:
        entry["hooks"] = [
            h for h in entry.get("hooks", []) if h.get("command") not in (command, hook)
        ]
    settings["hooks"]["Stop"] = [e for e in stop if e.get("hooks")]
    if not settings["hooks"]["Stop"]:
        del settings["hooks"]["Stop"]
    action = "removed Stop hook"
elif present:
    print("  Stop hook already registered")
    sys.exit(0)
else:
    stop.append({"hooks": [{"type": "command", "command": command}]})
    action = "registered Stop hook"

# Never overwrite an existing backup: install and uninstall run within the
# same second would otherwise destroy the pre-install copy.
backup = "%s.bak-ccyank-%d" % (path, time.time())
suffix = 1
while os.path.exists(backup):
    suffix += 1
    backup = "%s.bak-ccyank-%d-%d" % (path, time.time(), suffix)
shutil.copy2(path, backup)
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(settings, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp, path)
print("  %s (backup: %s)" % (action, os.path.basename(backup)))
PY

# ------------------------------------------------------------- ~/.tmux.conf
echo "tmux"
if ! command -v tmux >/dev/null 2>&1; then
    say "tmux not installed — the keybinding is unavailable."
    say "cc-yank still works as a CLI: $REPO/bin/cc-yank"
elif [ "$uninstall" -eq 1 ]; then
    if [ -f "$TMUX_CONF" ] && grep -qF "$MARK_BEGIN" "$TMUX_CONF"; then
        tmp=$(mktemp)
        sed "/^$MARK_BEGIN\$/,/^$MARK_END\$/d" "$TMUX_CONF" > "$tmp"
        mv "$tmp" "$TMUX_CONF"
        say "removed binding from $TMUX_CONF"
    else
        say "no cc-yank binding found"
    fi
elif [ -f "$TMUX_CONF" ] && grep -qF "$MARK_BEGIN" "$TMUX_CONF"; then
    say "binding already present"
else
    cat >> "$TMUX_CONF" <<EOF
$MARK_BEGIN
# prefix + y copies Claude Code's last answer. Prefix-based on purpose: a raw
# binding would swallow the key before Claude Code's TUI ever sees it.
bind-key y run-shell -b '"$REPO/bin/cc-yank" #{pane_id}'
$MARK_END
EOF
    say "added prefix + y binding to $TMUX_CONF"
fi

# set-clipboard drives OSC 52, the only thing that reaches a clipboard over
# SSH. `on` and `external` both work; only `off` breaks cc-yank.
if command -v tmux >/dev/null 2>&1 && [ "$uninstall" -eq 0 ]; then
    clip=$(tmux show-options -gv set-clipboard 2>/dev/null || echo external)
    if [ "$clip" = "off" ]; then
        printf '%s\nset -g set-clipboard external\n%s\n' "$MARK_BEGIN" "$MARK_END" >> "$TMUX_CONF"
        say "set-clipboard was off — set to external (required for OSC 52)"
    else
        say "set-clipboard = $clip (ok)"
    fi
fi

echo
if [ "$uninstall" -eq 1 ]; then
    echo "Uninstalled. Restart Claude Code, and run: tmux kill-server"
else
    cat <<EOF
Installed. To activate:

  1. tmux source-file ~/.tmux.conf      (or start a fresh tmux)
  2. restart Claude Code *inside tmux*  — hooks load at session start
  3. ask something, then press: prefix (ctrl+b) then y

Claude Code must run inside tmux; that is where the keypress is caught.
EOF
fi
