#!/bin/sh
# Smoke tests for bin/cc-yank. POSIX sh, no bashisms (portable to dash and to
# macOS's default /bin/sh), runnable from any cwd, no tmux involved — TMUX and
# TMUX_PANE are unset so the clipboard backends are never exercised, only the
# inspection modes (--path/--list/--print/--clean) and argument handling.
set -eu

# ------------------------------------------------------------------- locate
# Resolve paths relative to this script, not the caller's cwd (install.sh
# uses the same CDPATH= cd -- pattern for the same reason).
SELF_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$SELF_DIR/.." && pwd)
CLI="$ROOT/bin/cc-yank"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

XDG_STATE_HOME="$TMP/xdg-state"
export XDG_STATE_HOME
STATE_DIR="$XDG_STATE_HOME/cc-yank"

# No tmux in this environment: forces the stderr path in cc-yank's notify(),
# and keeps every clipboard backend out of reach.
unset TMUX || true
unset TMUX_PANE || true

n=0

pass() {
    n=$((n + 1))
    printf 'ok %d - %s\n' "$n" "$1"
}

fail() {
    printf 'not ok - %s\n' "$1" >&2
    exit 1
}

assert_eq() {
    # assert_eq DESC EXPECTED ACTUAL
    if [ "$2" = "$3" ]; then
        pass "$1"
    else
        printf '  expected: %s\n  actual:   %s\n' "$2" "$3" >&2
        fail "$1"
    fi
}

assert_contains() {
    # assert_contains DESC HAYSTACK NEEDLE
    case "$2" in
        *"$3"*) pass "$1" ;;
        *) printf '  expected to contain: %s\n  actual: %s\n' "$3" "$2" >&2
           fail "$1" ;;
    esac
}

# Verifier for the >64KB --print case: checks size cap, the truncation
# notice, and that every content line before it is a *whole* line from the
# source (i.e. the cut landed on a line boundary, not mid-line). Written in
# python3 rather than shell text surgery — python3 is already a hard
# dependency of this project, shell string slicing on byte boundaries is not
# reliable enough to trust for this assertion.
cat > "$TMP/check_truncated.py" <<'PYEOF'
import re
import sys

MAX_BYTES = 65536
path = sys.argv[1]

with open(path, "rb") as fh:
    data = fh.read()

if len(data) > MAX_BYTES:
    print("output exceeds MAX_BYTES: %d" % len(data))
    sys.exit(1)

text = data.decode("utf-8")
marker = "[cc-yank: truncated"
idx = text.find(marker)
if idx == -1:
    print("truncation notice not found")
    sys.exit(1)
if not text[idx:].rstrip("\n").endswith("]"):
    print("truncation notice malformed")
    sys.exit(1)

content = text[:idx].rstrip("\n")
pat = re.compile(r"^line \d{5}: 0123456789ABCDEF$")
for line in content.split("\n"):
    if not pat.match(line):
        print("partial or corrupt line before notice: %r" % line)
        sys.exit(1)

print("ok")
PYEOF

# ---------------------------------------------------------------- --help
out=$("$CLI" --help 2>&1)
assert_contains "--help mentions --clean" "$out" "--clean"

# ------------------------------------------------------------- --version
out=$("$CLI" --version 2>&1)
assert_eq "--version prints cc-yank 0.2.0" "cc-yank 0.2.0" "$out"

# -------------------------------------------------------- unknown option
rc=0
"$CLI" --bogus >"$TMP/unknown.out" 2>"$TMP/unknown.err" || rc=$?
assert_eq "unknown option exits 2" "2" "$rc"

# ------------------------------------------------------------------ --path
out=$("$CLI" --path %3)
assert_eq "--path %3 prints pane-3.md path" "$STATE_DIR/pane-3.md" "$out"

# ------------------------------------------------ --list with empty dir
out=$("$CLI" --list 2>&1)
assert_eq "--list on empty dir" "no answers stored yet" "$out"

# --------------------------------------------- create two stored answers
mkdir -p "$STATE_DIR"
printf 'answer three\n' > "$STATE_DIR/pane-3.md"
printf 'answer four\n' > "$STATE_DIR/pane-4.md"
# Distinct mtimes without sleeping: python3's os.utime is the portable way to
# do this (GNU touch -d and BSD touch disagree on syntax; python3 does not).
python3 -c "
import os, sys, time
now = time.time()
os.utime(sys.argv[1], (now - 20, now - 20))
os.utime(sys.argv[2], (now - 5, now - 5))
" "$STATE_DIR/pane-3.md" "$STATE_DIR/pane-4.md"

out=$("$CLI" --list 2>&1)
first_line=$(printf '%s\n' "$out" | sed -n '1p')
second_line=$(printf '%s\n' "$out" | sed -n '2p')
assert_contains "--list newest (pane-4) first" "$first_line" "pane-4"
assert_contains "--list oldest (pane-3) second" "$second_line" "pane-3"

# --------------------------------------------------- --print exact bytes
"$CLI" --print %3 > "$TMP/print3.out"
if cmp -s "$TMP/print3.out" "$STATE_DIR/pane-3.md"; then
    pass "--print %3 matches file content exactly"
else
    fail "--print %3 matches file content exactly"
fi

# ---------------------------------------------- --print with truncation
python3 -c "
import sys
with open(sys.argv[1], 'w') as fh:
    for i in range(3000):
        fh.write('line %05d: 0123456789ABCDEF\n' % i)
" "$STATE_DIR/pane-8.md"
big_size=$(wc -c < "$STATE_DIR/pane-8.md" | tr -d ' ')
if [ "$big_size" -le 65536 ]; then
    fail "fixture for truncation test must exceed 65536 bytes (got $big_size)"
fi

"$CLI" --print %8 > "$TMP/print8.out"
out_size=$(wc -c < "$TMP/print8.out" | tr -d ' ')
if [ "$out_size" -le 65536 ]; then
    pass "--print %8 output is within MAX_BYTES ($out_size <= 65536)"
else
    fail "--print %8 output is within MAX_BYTES ($out_size <= 65536)"
fi

check_out=$(python3 "$TMP/check_truncated.py" "$TMP/print8.out") || {
    printf '%s\n' "$check_out" >&2
    fail "--print %8 truncation notice and line-boundary cut"
}
assert_eq "--print %8 truncation notice and line-boundary cut" "ok" "$check_out"

# -------------------------------------------------------------- --clean
count_before=$(find "$STATE_DIR" -maxdepth 1 -name '*.md' | wc -l | tr -d ' ')
out=$("$CLI" --clean 2>&1)
assert_eq "--clean reports removed count" "removed $count_before stored answer(s)" "$out"
remaining=$(find "$STATE_DIR" -maxdepth 1 -name '*.md' | wc -l | tr -d ' ')
assert_eq "--clean leaves no .md files" "0" "$remaining"

# --------------------------------------------- --print with no file
rc=0
out=$("$CLI" --print %9 2>"$TMP/print9.err") || rc=$?
assert_eq "--print %9 (no file) exits 0" "0" "$rc"
assert_eq "--print %9 (no file) prints nothing to stdout" "" "$out"
err=$(cat "$TMP/print9.err")
assert_contains "--print %9 (no file) reports on stderr" "$err" "nothing to copy yet"

# --------------------------------------------------------------- summary
printf '\n%d/%d tests passed\n' "$n" "$n"
