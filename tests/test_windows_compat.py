"""Cross-platform helpers (shortclip/compat.py) and the ShortClip.exe launcher's python-like dispatch."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shortclip import compat  # noqa: E402


class PidAliveTests(unittest.TestCase):
    def test_self_is_alive(self):
        self.assertTrue(compat.pid_alive(os.getpid()))

    def test_finished_child_is_not_alive(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.assertFalse(compat.pid_alive(proc.pid))

    def test_garbage_is_not_alive(self):
        for bad in (None, "", "x", 0, -1):
            self.assertFalse(compat.pid_alive(bad))

    def test_probe_does_not_kill(self):
        """On Windows os.kill(pid, 0) terminates the process: the probe must leave it running."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(compat.pid_alive(proc.pid))
            self.assertIsNone(proc.poll())
        finally:
            proc.kill()
            proc.wait()


class LockTests(unittest.TestCase):
    def test_second_lock_from_other_process_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.lock"
            with path.open("w") as handle:
                self.assertTrue(compat.try_lock(handle))
                code = ("import sys; sys.path.insert(0, sys.argv[1]); from shortclip import compat; "
                        "h = open(sys.argv[2], 'a'); sys.exit(0 if compat.try_lock(h) else 3)")
                other = subprocess.run([sys.executable, "-c", code, str(ROOT), str(path)])
                self.assertEqual(other.returncode, 3)
                compat.unlock(handle)
            again = subprocess.run([sys.executable, "-c", code, str(ROOT), str(path)])
            self.assertEqual(again.returncode, 0)


class ResolveCmdTests(unittest.TestCase):
    def test_posix_leaves_command_alone(self):
        if compat.WINDOWS:
            self.skipTest("POSIX behaviour")
        self.assertEqual(compat.resolve_cmd(["npx", "hyperframes"]), ["npx", "hyperframes"])

    def test_paths_are_not_touched(self):
        cmd = [sys.executable, "-V"]
        self.assertEqual(compat.resolve_cmd(cmd), cmd)


class ClaudePromptTests(unittest.TestCase):
    def test_prompt_goes_on_stdin_on_windows(self):
        import shortclip.external as ext
        seen = []

        def fake_run(cmd, *, cwd, log_path, timeout, env=None, stdin_text=None):
            seen.append((cmd, stdin_text))
            log_path.write_text('{"subtype":"success","usage":{},"total_cost_usd":0}')
            return {}
        orig_run, orig_env, orig_win = ext.run_process, ext.process_env, ext.WINDOWS
        self.addCleanup(setattr, ext, "run_process", orig_run)
        self.addCleanup(setattr, ext, "process_env", orig_env)
        self.addCleanup(setattr, ext, "WINDOWS", orig_win)
        ext.run_process, ext.process_env = fake_run, lambda require_key=True: {}
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "c.log"
            prompt = "บรรทัดหนึ่ง\nline two"
            ext.WINDOWS = True
            ext.claude_json(prompt, model="m", cwd=Path(tmp), log_path=log, max_turns=1)
            ext.WINDOWS = False
            ext.claude_json(prompt, model="m", cwd=Path(tmp), log_path=log, max_turns=1)
        (win_cmd, win_in), (posix_cmd, posix_in) = seen
        self.assertNotIn(prompt, win_cmd)
        self.assertEqual(win_in, prompt)
        self.assertEqual(posix_cmd[:3], ["claude", "-p", prompt])
        self.assertIsNone(posix_in)

    def test_run_process_feeds_stdin_and_logs_utf8(self):
        import shortclip.external as ext
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "p.log"
            out = ext.run_process([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                                  cwd=Path(tmp), log_path=log, timeout=30, env=dict(os.environ),
                                  stdin_text="สวัสดี")
            self.assertIn("สวัสดี", out["tail"])


class LauncherDispatchTests(unittest.TestCase):
    """`ShortClip.exe` is sys.executable in the frozen app, so it must accept what the code passes to python."""

    def run_launcher(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(ROOT / "launcher.py"), *args], capture_output=True, text=True,
                              encoding="utf-8", cwd=cwd or ROOT, timeout=120,
                              env={**os.environ, "PYTHONUTF8": "1"})

    def test_dash_c(self):
        r = self.run_launcher("-c", "import sys; print(sys.argv)", "a", "b")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "['-c', 'a', 'b']")

    def test_script_with_unbuffered_flag_and_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "helper_mod.py"
            helper.write_text("VALUE = 'ไทย'\n", encoding="utf-8")
            script = Path(tmp) / "s.py"
            script.write_text("import sys, helper_mod\nprint(helper_mod.VALUE, sys.argv[1:])\nsys.exit(4)\n",
                              encoding="utf-8")
            r = self.run_launcher("-u", str(script), "x")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(r.stdout.strip(), "ไทย ['x']")

    def test_dash_m(self):
        r = self.run_launcher("-m", "json.tool", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage", r.stdout.lower())

    def test_orchestrator_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_launcher("orchestrator", "--db", str(Path(tmp) / "t.db"), "status")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unknown_command(self):
        r = self.run_launcher("nope")
        self.assertEqual(r.returncode, 2)

    def test_doctor_lists_every_tool(self):
        sys.path.insert(0, str(ROOT))
        import launcher
        buf = io.StringIO()
        with redirect_stdout(buf):
            launcher.print_doctor()
        for name in ("ffmpeg", "ffprobe", "Node.js", "Claude Code", "Chromium"):
            self.assertIn(name, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
