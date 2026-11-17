"""Contracts that the stubbed state-machine tests cannot see."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import shortclip.pipeline as pipeline_mod
from shortclip.pipeline import Pipeline
from shortclip.validate import verify_composition
from test_system import REAL_SCRIPT, StateMachineTests

ROOT = Path(__file__).resolve().parent.parent

SHOTS = {"duration": 12.0, "shots": [
    {"line": "l01", "start": 0.3, "end": 2.5, "subtitle": "หนึ่ง"},
    {"line": "l02", "start": 2.5, "end": 5.0, "subtitle": "สอง"},
    {"line": "l03", "start": 5.0, "end": 8.0, "subtitle": "สาม"},
]}
HTML = """<div id="root" data-composition-id="t" data-start="0" data-duration="9.9">
<div id="chipA" class="clip chip" data-line="l02" data-start="9" data-duration="9">x</div>
<p id="sub1" class="clip sub" data-start="9" data-duration="9">หนึ่ง</p>
<p id="sub2" class="clip sub" data-start="9" data-duration="9">สอง</p>
<p id="sub3" class="clip sub" data-start="9" data-duration="9">สาม</p>
<div id="end1" class="clip big" data-endcard data-start="9" data-duration="9">EP</div>
</div><script>const W=1080,H=1920,DUR=9.9;
const SH=[9,9,9,9];</script>
"""


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RetimeContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ep = Path(self.tmp.name) / "ep9"
        (self.ep / "composition").mkdir(parents=True)
        (self.ep / "shots.json").write_text(json.dumps(SHOTS, ensure_ascii=False))
        self.html = self.ep / "composition/index.html"
        self.html.write_text(HTML)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stale_timing_is_reported(self):
        problems = verify_composition(HTML, SHOTS)
        self.assertTrue(any("DUR" in p for p in problems))
        self.assertTrue(any("sub2" in p for p in problems))

    def test_retime_fixes_any_episode_without_hardcoded_line_ids(self):
        run = subprocess.run([sys.executable, str(ROOT / "tools/retime.py"), str(self.ep)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = self.html.read_text()
        self.assertEqual(verify_composition(out, SHOTS), [])
        self.assertIn('data-line="l02" data-start="2.5" data-duration="2.5"', out)
        self.assertIn('data-endcard data-start="8.4" data-duration="3.6"', out)
        self.assertIn("const SH=[0.3,2.5,5,8.4];", out)
        self.assertTrue((self.ep / "backup/index.pretime.html").exists())

    def test_changed_subtitle_text_is_not_fixable_by_retime(self):
        bad = HTML.replace(">สอง<", ">สอ<")
        self.assertTrue(any("sub2" in p and "text" in p for p in verify_composition(bad, SHOTS)))

    def test_delivered_episode_still_satisfies_contract(self):
        ep = ROOT / "examples/zombie-school/ep1v2"
        shots = json.loads((ep / "shots.json").read_text())
        self.assertEqual(verify_composition((ep / "composition/index.html").read_text(), shots), [])


class SoundCueTests(unittest.TestCase):
    def test_bell_cues_do_not_leak_into_other_episodes(self):
        tts = load_tool("tts_natural")
        self.assertEqual(tts.sfx_cues({"series": "zombie-school", "episode": 2}), {})
        self.assertEqual(tts.sfx_cues({"series": "zombie-school", "episode": 1}), {})
        self.assertEqual(tts.sfx_cues({"series": "x", "episode": 1, "sfx_cues": {"l04": "impact/deep"}}),
                         {"l04": "impact/deep"})


class PaidPassTests(unittest.TestCase):
    """Real writer/animator code paths with Claude replaced by a counting fake."""

    handlers = StateMachineTests.handlers
    create = StateMachineTests.create

    def setUp(self):
        StateMachineTests.setUp(self)
        self.claude_calls = []
        self.epdir = self.root / "projects/zombie-school/ep1"
        self._orig = pipeline_mod.claude_json
        pipeline_mod.claude_json = self.fake_claude
        for role in ("writer", "animator"):
            self.pipe.handlers.pop(role)
        self.pipe._finish_composition = lambda *a, **k: {"finished": True}
        (self.root / "CLAUDE.md").write_text("rules")
        (self.root / "agents").mkdir()
        shutil.copy2(ROOT / "agents/writer.md", self.root / "agents/writer.md")

    def tearDown(self):
        pipeline_mod.claude_json = self._orig
        StateMachineTests.tearDown(self)

    def fake_claude(self, prompt, *, cwd, log_path, **kw):
        self.claude_calls.append(prompt)
        if Path(cwd).name == "composition":
            (Path(cwd) / "index.html").write_text(f"<html>{len(self.claude_calls)}</html>")
        else:
            data = json.loads(json.dumps(REAL_SCRIPT))
            data["logline"] = f"draft {len(self.claude_calls)}"
            (self.epdir / "script.json").write_text(json.dumps(data, ensure_ascii=False))
        result = {"subtype": "success", "num_turns": 1, "total_cost_usd": 0.5,
                  "usage": {"output_tokens": 100}}
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(result))
        return result

    def cost(self, state, role):
        return next(s for s in state["steps"] if s["role"] == role)["cost_usd"]

    def test_gate1_reject_pays_for_a_real_rewrite_and_keeps_both_costs(self):
        jid = self.create()["job"]["id"]
        self.assertEqual(len(self.claude_calls), 1)
        state = self.pipe.advance(jid, "reject", "hook ไม่ชัด")
        self.assertEqual(len(self.claude_calls), 2)
        self.assertIn("hook ไม่ชัด", self.claude_calls[1])
        self.assertAlmostEqual(self.cost(state, "writer"), 1.0)
        self.assertEqual(json.loads((self.epdir / "script.json").read_text())["logline"], "draft 2")
        self.assertTrue(list((self.epdir / "backup").glob("script.rejected-*.json")))

    def test_crash_after_claude_finished_does_not_pay_twice(self):
        jid = self.create()["job"]["id"]
        self.pipe.con.execute("UPDATE jobs SET status='failed',gate=NULL WHERE id=?", (jid,))
        self.pipe.con.execute("UPDATE steps SET status='failed' WHERE job_id=? AND role='writer'", (jid,))
        state = self.pipe.resume(jid)
        self.assertEqual(len(self.claude_calls), 1)
        self.assertAlmostEqual(self.cost(state, "writer"), 0.5)

    def test_gate2_reject_polishes_once_and_adds_cost(self):
        jid = self.create()["job"]["id"]
        state = self.pipe.advance(jid, "approve")
        self.assertEqual(len(self.claude_calls), 2)
        self.assertAlmostEqual(self.cost(state, "animator"), 0.5)
        state = self.pipe.advance(jid, "reject", "ซับทับเท้า")
        self.assertEqual(len(self.claude_calls), 3)
        self.assertIn("ซับทับเท้า", self.claude_calls[2])
        self.assertAlmostEqual(self.cost(state, "animator"), 1.0)
        self.assertTrue(list((self.epdir / "backup").glob("index.before-polish-*.html")))
        # a crash after the polish finished must not polish (and pay) again
        self.pipe.con.execute("UPDATE jobs SET status='failed',gate=NULL WHERE id=?", (jid,))
        self.pipe.con.execute("UPDATE steps SET status='failed' WHERE job_id=? AND role='animator'", (jid,))
        self.pipe.con.execute("UPDATE steps SET status='queued' WHERE job_id=? AND role IN ('editor','qa')", (jid,))
        self.pipe.resume(jid)
        self.assertEqual(len(self.claude_calls), 3)


if __name__ == "__main__":
    unittest.main()
