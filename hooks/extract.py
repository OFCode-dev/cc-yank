#!/usr/bin/env python3
"""Extract the readable text of Claude Code's last answer from a transcript.

Runs as a Stop hook. Reads the hook payload on stdin, tails the session
transcript backwards, and writes the answer's markdown source to the state
directory so a keypress can deliver it instantly.

Never writes to stdout and always exits 0 — a crashing hook must not disturb
the session.
"""

import json
import os
import sys
import time

CHUNK = 256 * 1024
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "cc-yank",
)


def iter_lines_reverse(path, chunk_size=CHUNK):
    """Yield complete lines from the end of a file backwards.

    Transcripts reach 100 MB+; reading one forwards to find its tail would
    cost more than the answer is worth.
    """
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        tail = b""
        while pos > 0:
            read_size = min(chunk_size, pos)
            pos -= read_size
            fh.seek(pos)
            buf = fh.read(read_size) + tail
            lines = buf.split(b"\n")
            # The first element may be a partial line continuing into the
            # chunk before it; hold it back until that chunk is read.
            tail = lines.pop(0)
            for line in reversed(lines):
                if line.strip():
                    yield line
        if tail.strip():
            yield tail


def blocks_of(entry):
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def is_human_turn(entry):
    """True for a genuine user message, false for tool output.

    Tool results arrive as `user` entries. Treating them as a turn boundary
    would truncate every answer that used a tool.
    """
    blocks = blocks_of(entry)
    if not blocks:
        return False
    return any(b.get("type") != "tool_result" for b in blocks)


def extract_answer(lines):
    """Collect text blocks of the final assistant turn, oldest-first."""
    parts = []
    for raw in lines:
        try:
            entry = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(entry, dict):
            continue
        # Subagent conversations are a separate stream; they are not what the
        # user just read.
        if entry.get("isSidechain"):
            continue

        kind = entry.get("type")
        if kind == "assistant":
            for block in blocks_of(entry):
                # thinking and tool_use are not the answer.
                if block.get("type") == "text":
                    text = (block.get("text") or "").strip()
                    if text:
                        parts.append(text)
        elif kind == "user":
            if is_human_turn(entry):
                break
        # Every other type — attachment, system, mode, ai-title,
        # file-history-*, last-prompt, … — is metadata. Skip silently.

    parts.reverse()
    return "\n\n".join(parts)


def tmux_pane():
    """Find the tmux pane this session lives in.

    Claude Code spawns hooks with a sanitised environment, so TMUX_PANE is
    normally absent here even when the session is inside tmux. Recover it from
    an ancestor process. Linux-only; elsewhere we fall back to a shared file.
    """
    pane = os.environ.get("TMUX_PANE")
    if pane:
        return pane
    try:
        pid = os.getpid()
        for _ in range(8):
            with open("/proc/%d/stat" % pid) as fh:
                # Field 4 is ppid, but comm (field 2) can contain spaces and
                # parentheses — split after the final ')'.
                pid = int(fh.read().rsplit(")", 1)[1].split()[1])
            if pid <= 1:
                break
            with open("/proc/%d/environ" % pid, "rb") as fh:
                for kv in fh.read().split(b"\0"):
                    if kv.startswith(b"TMUX_PANE="):
                        return kv.split(b"=", 1)[1].decode("utf-8", "replace")
    except (OSError, ValueError, IndexError):
        pass
    return ""


def stable_extract(path, settle=0.6, step=0.2, tries=8):
    """Read the answer once the transcript has stopped growing.

    Stop fires before Claude Code has flushed the turn's final text block, so
    reading immediately loses the last paragraph. Wait, then re-read until two
    consecutive passes agree. The hook runs in the background long before the
    key is pressed, so a second or two costs nothing.
    """
    time.sleep(settle)
    previous = extract_answer(iter_lines_reverse(path))
    for _ in range(tries):
        time.sleep(step)
        current = extract_answer(iter_lines_reverse(path))
        if current == previous:
            return current
        previous = current
    return previous


def state_path():
    """One file per tmux pane so concurrent sessions don't clobber each other."""
    pane = tmux_pane()
    name = "pane-%s.md" % pane.lstrip("%") if pane else "default.md"
    return os.path.join(STATE_DIR, name)


def log_error(exc):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "error.log"), "a") as fh:
            fh.write("%s: %s\n" % (type(exc).__name__, exc))
    except OSError:
        pass


def write_answer(text, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    # Atomic: a keypress landing mid-write must not read a half-written file.
    os.replace(tmp, dest)


def main():
    argv = sys.argv[1:]
    if "--transcript" in argv:
        transcript = argv[argv.index("--transcript") + 1]
    else:
        payload = json.loads(sys.stdin.read() or "{}")
        transcript = payload.get("transcript_path")

    if not transcript or not os.path.exists(transcript):
        return

    answer = stable_extract(transcript)
    if not answer:
        return

    write_answer(answer, state_path())

    if "--stdout" in argv:
        sys.stdout.write(answer)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # a hook must never break the session
        log_error(exc)
    sys.exit(0)
