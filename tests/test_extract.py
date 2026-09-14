#!/usr/bin/env python3
"""Unit tests for hooks/extract.py.

unittest + stdlib only, matching the project's no-dependencies rule. extract.py
is imported by path rather than installed, so this file can run straight out
of a checkout: `python3 -m unittest discover -s tests` or plain `pytest`-free
`python3 tests/test_extract.py`.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import extract  # noqa: E402  (import after sys.path fixup, by design)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRACT_PY = os.path.join(REPO_ROOT, "hooks", "extract.py")


# --------------------------------------------------------------------- helpers

def human_str(text):
    return {"type": "user", "message": {"content": text}}


def human_arr(text):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def assistant_text(text, sidechain=False):
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {"content": [{"type": "text", "text": text}]},
    }


def assistant_thinking(text):
    return {
        "type": "assistant",
        "isSidechain": False,
        "message": {"content": [{"type": "thinking", "text": text}]},
    }


def assistant_tool_use(name="Bash"):
    return {
        "type": "assistant",
        "isSidechain": False,
        "message": {"content": [{"type": "tool_use", "name": name}]},
    }


def tool_result(text):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": text}]}}


def meta(kind):
    return {"type": kind}


def reversed_lines(entries):
    """entries in chronological order -> raw JSON lines, newest first.

    This is the shape extract_answer expects: it is always fed the output of
    iter_lines_reverse, i.e. reverse-chronological.
    """
    return [json.dumps(e) for e in reversed(entries)]


def write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


# --------------------------------------------------------------- extract_answer

class TestExtractAnswer(unittest.TestCase):
    def test_single_text_answer_drops_thinking_and_tool_use(self):
        entries = [
            human_str("question"),
            assistant_thinking("pondering..."),
            assistant_tool_use(),
            assistant_text("The answer."),
        ]
        self.assertEqual(extract.extract_answer(reversed_lines(entries)), "The answer.")

    def test_answer_split_around_tool_calls_stops_at_string_human(self):
        entries = [
            human_str("first question"),
            assistant_text("Part one."),
            assistant_tool_use(),
            tool_result("tool output"),
            assistant_text("Part two."),
        ]
        result = extract.extract_answer(reversed_lines(entries))
        self.assertEqual(result, "Part one.\n\nPart two.")

    def test_answer_split_around_tool_calls_stops_at_array_human(self):
        entries = [
            assistant_text("Old answer from a previous turn."),
            human_arr("first question"),
            assistant_text("Part one."),
            assistant_tool_use(),
            tool_result("tool output"),
            assistant_text("Part two."),
        ]
        result = extract.extract_answer(reversed_lines(entries))
        # The previous turn's answer must not leak in.
        self.assertEqual(result, "Part one.\n\nPart two.")

    def test_sidechain_metadata_and_malformed_lines_ignored(self):
        lines = [
            json.dumps(meta("attachment")),
            json.dumps(assistant_text("Should be ignored (subagent)", sidechain=True)),
            "{not valid json",
            json.dumps(assistant_text("Real answer")),
        ]
        result = extract.extract_answer(list(reversed(lines)))
        self.assertEqual(result, "Real answer")

    def test_no_lines_gives_empty_string(self):
        self.assertEqual(extract.extract_answer([]), "")


# ------------------------------------------------------------------ is_human_turn

class TestIsHumanTurn(unittest.TestCase):
    def test_tool_result_only_is_not_human(self):
        self.assertFalse(extract.is_human_turn(tool_result("x")))

    def test_string_content_is_human(self):
        self.assertTrue(extract.is_human_turn(human_str("hi")))

    def test_array_text_content_is_human(self):
        self.assertTrue(extract.is_human_turn(human_arr("hi")))

    def test_mixed_tool_result_and_text_is_human(self):
        entry = {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "x"}, {"type": "text", "text": "y"}]},
        }
        self.assertTrue(extract.is_human_turn(entry))

    def test_empty_content_list_is_not_human(self):
        self.assertFalse(extract.is_human_turn({"type": "user", "message": {"content": []}}))

    def test_missing_message_is_not_human(self):
        self.assertFalse(extract.is_human_turn({"type": "user"}))


# --------------------------------------------------------------- iter_lines_reverse

class TestIterLinesReverse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identical_across_chunk_sizes_utf8_no_trailing_newline_blank_lines(self):
        lines = [
            "birinci satir: bir seker, iki seker",
            "",  # blank line: iter_lines_reverse must skip it
            "ucuncu satir with multibyte: ş ğ ç ö ü İ",
            "   ",  # whitespace-only line: also skipped
            "son satir, no trailing newline after this",
        ]
        # No trailing newline at EOF, on purpose.
        content = "\n".join(lines).encode("utf-8")
        path = os.path.join(self.tmp, "transcript.jsonl")
        with open(path, "wb") as fh:
            fh.write(content)

        expected = [
            "son satir, no trailing newline after this",
            "ucuncu satir with multibyte: ş ğ ç ö ü İ",
            "birinci satir: bir seker, iki seker",
        ]

        results = {}
        for chunk_size in (256 * 1024, 64, 7):
            got = [line.decode("utf-8") for line in extract.iter_lines_reverse(path, chunk_size)]
            results[chunk_size] = got

        for chunk_size, got in results.items():
            self.assertEqual(got, expected, "mismatch at chunk_size=%d" % chunk_size)

    def test_trailing_newline_variant_also_matches(self):
        content = b"one\ntwo\nthree\n"
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "wb") as fh:
            fh.write(content)
        expected = ["three", "two", "one"]
        for chunk_size in (256 * 1024, 64, 7):
            got = [line.decode("utf-8") for line in extract.iter_lines_reverse(path, chunk_size)]
            self.assertEqual(got, expected)


# --------------------------------------------------------------------- caught_up

class TestCaughtUp(unittest.TestCase):
    def test_whitespace_insensitive_suffix_match(self):
        answer = "line one\n\nline   two"
        self.assertTrue(extract.caught_up(answer, "line two"))

    def test_false_when_last_message_empty(self):
        self.assertFalse(extract.caught_up("some answer text", ""))
        self.assertFalse(extract.caught_up("some answer text", None))

    def test_false_when_answer_empty(self):
        self.assertFalse(extract.caught_up("", "something"))

    def test_false_when_not_a_suffix(self):
        self.assertFalse(extract.caught_up("hello world", "world!!"))


# ------------------------------------------------------------------------ settle

class TestSettle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "transcript.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_already_complete_returns_immediately(self):
        last_message = "The final answer."
        write_jsonl(self.path, [human_str("q"), assistant_text(last_message)])
        start = time.monotonic()
        answer = extract.settle(self.path, last_message, max_wait=2)
        elapsed = time.monotonic() - start
        self.assertEqual(answer, last_message)
        self.assertLess(elapsed, 0.3)

    def test_lagging_transcript_is_picked_up_once_it_arrives(self):
        last_message = "The final answer, delivered late."
        write_jsonl(self.path, [human_str("q")])  # nothing assistant-side yet

        def append_final():
            time.sleep(0.3)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(assistant_text(last_message)) + "\n")

        t = threading.Thread(target=append_final)
        t.start()
        try:
            answer = extract.settle(self.path, last_message, max_wait=2)
        finally:
            t.join()
        self.assertEqual(answer, last_message)

    def test_lagging_and_never_arrives_returns_partial_then_complete_appends(self):
        last_message = "Full final answer that never lands in time."
        write_jsonl(self.path, [human_str("q"), assistant_text("Partial start")])
        start = time.monotonic()
        answer = extract.settle(self.path, last_message, max_wait=0.3)
        elapsed = time.monotonic() - start
        self.assertEqual(answer, "Partial start")
        self.assertLess(elapsed, 1.0)
        completed = extract.complete(answer, last_message)
        self.assertEqual(completed, "Partial start\n\n" + last_message)

    def test_no_last_message_settles_after_quiet_period(self):
        write_jsonl(self.path, [human_str("q"), assistant_text("A static, already-complete answer.")])
        start = time.monotonic()
        answer = extract.settle(self.path, None, max_wait=3)
        elapsed = time.monotonic() - start
        self.assertEqual(answer, "A static, already-complete answer.")
        # QUIET_POLLS(6) * POLL(0.1) ~= 0.6s before the quiet path returns.
        self.assertGreaterEqual(elapsed, 0.45)
        self.assertLess(elapsed, 1.5)


# ----------------------------------------------------------------------- complete

class TestComplete(unittest.TestCase):
    def test_no_duplication_when_already_caught_up(self):
        answer = "Some text foo bar"
        self.assertEqual(extract.complete(answer, "foo bar"), answer)

    def test_empty_answer_returns_last_message_alone(self):
        self.assertEqual(extract.complete("", "hi"), "hi")

    def test_appends_when_not_caught_up(self):
        self.assertEqual(extract.complete("partial", "final"), "partial\n\nfinal")

    def test_no_last_message_returns_answer_unchanged(self):
        self.assertEqual(extract.complete("x", ""), "x")
        self.assertEqual(extract.complete("", ""), "")


# -------------------------------------------------------------------------- prune

class TestPrune(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_state_dir = extract.STATE_DIR
        extract.STATE_DIR = self.tmp

    def tearDown(self):
        extract.STATE_DIR = self._orig_state_dir
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_removed_recent_kept_non_md_untouched(self):
        now = time.time()
        old_md = os.path.join(self.tmp, "pane-1.md")
        new_md = os.path.join(self.tmp, "pane-2.md")
        other = os.path.join(self.tmp, "error.log")
        for p in (old_md, new_md, other):
            with open(p, "w") as fh:
                fh.write("x")
        os.utime(old_md, (now - 40 * 86400, now - 40 * 86400))
        os.utime(new_md, (now - 1 * 86400, now - 1 * 86400))
        os.utime(other, (now - 40 * 86400, now - 40 * 86400))

        extract.prune(keep_days=30, now=now)

        self.assertFalse(os.path.exists(old_md))
        self.assertTrue(os.path.exists(new_md))
        self.assertTrue(os.path.exists(other))


# ---------------------------------------------------------------------- write_answer

class TestWriteAnswer(unittest.TestCase):
    def test_atomic_no_tmp_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "sub", "pane-1.md")
            extract.write_answer("hello there", dest)
            with open(dest, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "hello there")
            self.assertFalse(os.path.exists(dest + ".tmp"))


# ----------------------------------------------------------------------- end-to-end

class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state_home = os.path.join(self.tmp, "xdg-state")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env(self, **extra):
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = self.state_home
        env["CC_YANK_SETTLE_MAX"] = "0.3"
        env.pop("TMUX_PANE", None)
        env.update(extra)
        return env

    def test_full_payload_writes_pane_file_exit0_empty_stdout(self):
        transcript = os.path.join(self.tmp, "t.jsonl")
        write_jsonl(transcript, [human_str("q"), assistant_text("The final answer.")])
        payload = {"transcript_path": transcript, "last_assistant_message": "The final answer."}
        proc = subprocess.run(
            [sys.executable, EXTRACT_PY],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=self._env(TMUX_PANE="%7"),
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        pane_file = os.path.join(self.state_home, "cc-yank", "pane-7.md")
        self.assertTrue(os.path.exists(pane_file))
        with open(pane_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "The final answer.")

    def test_missing_transcript_path_falls_back_to_payload_text(self):
        payload = {"last_assistant_message": "Just the payload text."}
        proc = subprocess.run(
            [sys.executable, EXTRACT_PY],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=self._env(TMUX_PANE="%4"),
        )
        self.assertEqual(proc.returncode, 0)
        pane_file = os.path.join(self.state_home, "cc-yank", "pane-4.md")
        self.assertTrue(os.path.exists(pane_file))
        with open(pane_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "Just the payload text.")

    def test_stdout_flag_echoes_answer(self):
        transcript = os.path.join(self.tmp, "t2.jsonl")
        write_jsonl(transcript, [human_str("q"), assistant_text("Stdout echoed answer.")])
        proc = subprocess.run(
            [
                sys.executable, EXTRACT_PY,
                "--transcript", transcript,
                "--last-message", "Stdout echoed answer.",
                "--stdout",
            ],
            input="",
            capture_output=True,
            text=True,
            env=self._env(TMUX_PANE="%5"),
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "Stdout echoed answer.")

    def test_nonexistent_transcript_and_no_last_message_writes_nothing(self):
        payload = {"transcript_path": os.path.join(self.tmp, "does-not-exist.jsonl")}
        proc = subprocess.run(
            [sys.executable, EXTRACT_PY],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=self._env(TMUX_PANE="%6"),
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        cc_yank_dir = os.path.join(self.state_home, "cc-yank")
        if os.path.exists(cc_yank_dir):
            md_files = [f for f in os.listdir(cc_yank_dir) if f.endswith(".md")]
            self.assertEqual(md_files, [])

    def test_version_flag(self):
        proc = subprocess.run(
            [sys.executable, EXTRACT_PY, "--version"],
            capture_output=True,
            text=True,
            env=self._env(),
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "cc-yank extract 0.2.0")


if __name__ == "__main__":
    unittest.main()
