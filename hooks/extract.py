#!/usr/bin/env python3
"""Extract the readable text of Claude Code's last answer from a transcript.

Runs as a Stop hook. Reads the hook payload on stdin, tails the session
transcript backwards, and writes the answer's markdown source to the state
directory so a keypress can deliver it instantly.

Never writes to stdout (unless asked with --stdout) and always exits 0 — a
crashing hook must not disturb the session.
"""

import json
import os
import sys
import tempfile
import time

VERSION = "0.2.0"
CHUNK = 256 * 1024
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "cc-yank",
)
# The transcript is written asynchronously and lags the hook. How long we are
# willing to wait for it, and how often we look. Overridable for tests.
SETTLE_MAX = float(os.environ.get("CC_YANK_SETTLE_MAX", "3"))
POLL = 0.1
QUIET_POLLS = 6          # legacy settle: this many unchanged polls in a row
KEEP_DAYS = 30           # stored answers older than this are pruned


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
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
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
            message_parts = []
            for block in blocks_of(entry):
                # thinking and tool_use are not the answer.
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    text = block["text"].strip()
                    if text:
                        message_parts.append(text)
            # Only entries run backwards; blocks within an entry are already
            # chronological and must stay together when parts is reversed.
            if message_parts:
                parts.append("\n\n".join(message_parts))
        elif kind == "user":
            if is_human_turn(entry):
                break
        # Every other type — attachment, system, mode, ai-title,
        # file-history-*, last-prompt, … — is metadata. Skip silently.

    parts.reverse()
    return "\n\n".join(parts)


def tmux_pane():
    """Find the tmux pane this session lives in.

    Claude Code passes TMUX_PANE through to hooks (measured on 2.1.270), so
    the environment is normally enough. Older versions scrubbed it; for them,
    recover it from an ancestor process. Linux-only; elsewhere we fall back
    to a shared file.
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


def squash(text):
    return " ".join(text.split())


def caught_up(answer, last_message):
    """True once the transcript holds the turn's final text block.

    The hook payload carries that block as `last_assistant_message`; when the
    extracted answer ends with it, nothing more is coming.
    """
    final_text = squash(last_message or "")
    return bool(final_text) and squash(answer).endswith(final_text)


def settle(path, last_message, max_wait=None):
    """Read the answer once the transcript has caught up with the turn.

    Stop fires before Claude Code has flushed the turn's text to the
    transcript — at fire time the file can hold no assistant line at all.
    Poll it, re-extracting only when its size changes, until either the final
    text block (`last_message`) appears or `max_wait` runs out.

    Without `last_message` (older Claude Code) the only signal is quiet: the
    file unchanged for QUIET_POLLS consecutive polls.
    """
    if max_wait is None:
        max_wait = SETTLE_MAX
    last_message = (last_message or "").strip()
    deadline = time.monotonic() + max_wait
    answer = ""
    size = None
    quiet = 0
    while True:
        try:
            current = os.stat(path).st_size
        except OSError:
            current = -1
        if current != size:
            quiet = 0
            try:
                candidate = extract_answer(iter_lines_reverse(path)) if current >= 0 else answer
            except OSError:
                # The file may disappear between stat and open. Retry even
                # if it comes back with the same size, retaining any text
                # already read so complete() can still add the final block.
                size = None
            else:
                size = current
                answer = candidate
            if caught_up(answer, last_message):
                return answer
        else:
            quiet += 1
            if not last_message and answer and quiet >= QUIET_POLLS:
                return answer
        if time.monotonic() >= deadline:
            return answer
        time.sleep(POLL)


def complete(answer, last_message):
    """Never lose the last paragraph.

    If the transcript was still behind at the deadline, the hook payload is
    the only copy of the final block. Append it rather than deliver an
    answer with its ending missing.
    """
    last_message = (last_message or "").strip()
    if not last_message or caught_up(answer, last_message):
        return answer
    return answer + "\n\n" + last_message if answer else last_message


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
    directory = os.path.dirname(dest)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    # Overlapping hooks need separate temporary files. mkstemp also keeps
    # answers private (0600) regardless of the user's umask.
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(dest) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        # A keypress landing mid-write must not read a half-written file.
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def prune(keep_days=KEEP_DAYS, now=None):
    """Drop stored answers nobody will ask for again.

    Pane ids are reused across tmux restarts, so the directory would otherwise
    grow one file per pane id ever seen, forever.
    """
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    try:
        names = os.listdir(STATE_DIR)
    except OSError:
        return
    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(STATE_DIR, name)
        try:
            if os.stat(path).st_mtime < cutoff:
                os.remove(path)
        except OSError:
            pass


def parse_args(argv):
    """Hook mode reads the payload from stdin; --transcript is for tests."""
    opts = {"transcript": None, "last_message": None, "stdout": False}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--transcript", "--last-message"):
            i += 1
            if i >= len(argv):
                sys.exit("extract.py: %s needs a value" % arg)
            opts[arg.lstrip("-").replace("-", "_")] = argv[i]
        elif arg == "--stdout":
            opts["stdout"] = True
        elif arg == "--version":
            print("cc-yank extract %s" % VERSION)
            sys.exit(0)
        i += 1
    return opts


def main():
    opts = parse_args(sys.argv[1:])
    if opts["transcript"]:
        transcript, last_message = opts["transcript"], opts["last_message"]
    else:
        payload = json.loads(sys.stdin.read() or "{}")
        transcript = payload.get("transcript_path")
        last_message = payload.get("last_assistant_message")

    if not transcript:
        # The payload still carries the final block; better than nothing.
        answer = complete("", last_message)
    else:
        answer = complete(settle(transcript, last_message), last_message)
    if not answer:
        return

    write_answer(answer, state_path())
    prune()

    if opts["stdout"]:
        sys.stdout.write(answer)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # a hook must never break the session
        log_error(exc)
    sys.exit(0)
