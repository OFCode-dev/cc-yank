"""Exercise installation in disposable homes, never the real user's config."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home_dir = self.root / "home"
        self.home_dir.mkdir()
        self.tools = self.root / "tools"
        self.tools.mkdir()
        # Keep tmux availability deterministic on both Linux and macOS.
        for name in ("python3", "chmod", "dirname", "mkdir", "grep", "sed",
                     "mktemp", "mv", "cat", "sh"):
            (self.tools / name).symlink_to(shutil.which(name))
        self.settings = self.home_dir / ".claude" / "settings.json"
        self.tmux_conf = self.home_dir / ".tmux.conf"
        self.env = dict(os.environ, HOME=str(self.home_dir), PATH=str(self.tools))

    def tmux_available(self):
        tmux = self.tools / "tmux"
        tmux.write_text('#!/bin/sh\nprintf "%s\\n" "${CC_TEST_CLIP:-external}"\n')
        tmux.chmod(0o755)

    def install(self, *args, repo=REPO):
        result = subprocess.run([str(repo / "install.sh"), *args],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_repeat_install_with_clipboard_off_is_idempotent(self):
        self.tmux_available()
        self.env["CC_TEST_CLIP"] = "off"
        self.install()
        settings = self.settings.read_bytes()
        tmux_conf = self.tmux_conf.read_bytes()
        backups = list(self.settings.parent.glob("*.bak-ccyank-*"))
        self.install()
        self.assertEqual(self.settings.read_bytes(), settings)
        self.assertEqual(self.tmux_conf.read_bytes(), tmux_conf)
        self.assertEqual(list(self.settings.parent.glob("*.bak-ccyank-*")), backups)

    def test_uninstall_removes_binding_without_tmux_and_preserves_other_config(self):
        self.tmux_available()
        original = {"theme": "dark", "hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "echo other hook"}]}
        ]}}
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps(original))
        self.tmux_conf.write_text("set -g mouse on\n")
        self.install()
        (self.tools / "tmux").unlink()
        result = self.install("--uninstall")
        self.assertEqual(json.loads(self.settings.read_text()), original)
        self.assertEqual(self.tmux_conf.read_text(), "set -g mouse on\n")
        self.assertNotIn("kill-server", result.stdout)

    def test_uninstall_on_clean_home_creates_no_config(self):
        self.install("--uninstall")
        self.assertFalse(self.settings.parent.exists())
        self.assertFalse(self.tmux_conf.exists())

    def test_update_preserves_private_settings_permissions_and_legacy_hooks(self):
        self.settings.parent.mkdir()
        hook = str(REPO / "hooks" / "extract.py")
        self.settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": '"%s"' % hook},
            {"type": "command", "command": "echo keep me"}
        ]}]}}))
        self.settings.chmod(0o600)
        self.install()
        hooks = json.loads(self.settings.read_text())["hooks"]["Stop"][0]["hooks"]
        self.assertEqual(hooks[0]["command"], shlex.quote(hook))
        self.assertEqual(hooks[1]["command"], "echo keep me")
        self.install("--uninstall")
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)
        hooks = json.loads(self.settings.read_text())["hooks"]["Stop"][0]["hooks"]
        self.assertEqual(hooks, [{"type": "command", "command": "echo keep me"}])

    def test_repository_path_is_quoted_for_hook_and_tmux_shell(self):
        repo = self.root / 'repo with spaces \'and "quotes" $cash'
        repo.mkdir()
        shutil.copy2(REPO / "install.sh", repo)
        for directory in ("hooks", "bin"):
            shutil.copytree(REPO / directory, repo / directory)
        self.tmux_available()
        self.install(repo=repo)
        # Upgrading an existing installation must repair its old binding too.
        self.tmux_conf.write_text("# >>> cc-yank >>>\n"
                                  + "bind-key y run-shell -b '\"%s\" #{pane_id}'\n" % (repo / "bin" / "cc-yank")
                                  + "# <<< cc-yank <<<\n")
        self.install(repo=repo)
        settings = json.loads(self.settings.read_text())
        command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(shlex.split(command), [str(repo / "hooks" / "extract.py")])
        result = subprocess.run(["sh", "-c", command + " --version"],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        binding = next(line for line in self.tmux_conf.read_text().splitlines()
                       if line.startswith("bind-key "))
        shell_command = shlex.split(binding)[-1]
        self.assertEqual(shlex.split(shell_command), [str(repo / "bin" / "cc-yank"), "#{pane_id}"])
        result = subprocess.run(["sh", "-c", shell_command.replace("#{pane_id}", "--version")],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
    def test_generated_binding_runs_through_real_tmux_parser(self):
        repo = self.root / 'repo \'quotes" $cash #{pane_id}'
        repo.mkdir()
        shutil.copy2(REPO / "install.sh", repo)
        for directory in ("hooks", "bin"):
            shutil.copytree(REPO / directory, repo / directory)
        output = self.root / "called.json"
        (repo / "bin" / "cc-yank").write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            + "with open(%r, 'w') as fh: json.dump(sys.argv[1:], fh)\n" % str(output)
        )
        self.tmux_available()
        self.install(repo=repo)
        executable = shutil.which("tmux")
        socket = str(self.root / "tmux.sock")
        prefix = [executable, "-S", socket]
        env = dict(self.env, TERM="xterm-256color", TMUX="", TMUX_PANE="")

        def run(*args):
            result = subprocess.run([*prefix, *args], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout.strip()

        # Start a private server, then exercise both configuration parsing and
        # run-shell's second parsing/formatting pass after a pane exists.
        try:
            run("-f", "/dev/null", "new-session", "-d", "-s", "test", "/bin/sh")
            run("source-file", str(self.tmux_conf))
            pane = run("display-message", "-p", "#{pane_id}")
            binding = next(line for line in self.tmux_conf.read_text().splitlines()
                           if line.startswith("bind-key "))
            command_file = self.root / "run.conf"
            command_file.write_text(binding.replace("bind-key y run-shell -b ", "run-shell ", 1) + "\n")
            run("source-file", str(command_file))
            self.assertEqual(json.loads(output.read_text()), [pane])
        finally:
            subprocess.run([*prefix, "kill-server"], env=env, capture_output=True)


if __name__ == "__main__":
    unittest.main()
