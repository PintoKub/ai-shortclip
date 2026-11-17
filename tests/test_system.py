import json
import shutil
import tempfile
import unittest
from pathlib import Path

from shortclip.db import connect, init_db
from shortclip.pipeline import Pipeline
from shortclip.validate import ValidationError, validate_script, validate_shots

ROOT = Path(__file__).resolve().parent.parent
REAL_SCRIPT = json.loads((ROOT / "examples/zombie-school/ep1v2/script.json").read_text())
REAL_SHOTS = json.loads((ROOT / "examples/zombie-school/ep1v2/shots.json").read_text())


def shots_for(script, duration=60.0):
    lines = script["lines"]
    first_end = 2.6
    remaining = duration - 5.0 - first_end
    weights = [max(1, len(x["text"])) for x in lines[1:]]
    total = sum(weights)
    rows = []
    cursor = 0.0
    for i, line in enumerate(lines):
        if i == 0:
            end = first_end
        elif i == len(lines) - 1:
            end = duration - 5.0
        else:
            end = cursor + remaining * weights[i - 1] / total
        rows.append({"shot": i + 1, "line": line["id"], "start": round(cursor, 3),
                     "end": round(end, 3), "subtitle": line["text"],
                     "on_screen": line.get("on_screen", ""), "visual": "test", "camera": "test"})
        cursor = end - 0.03
    return {"series": script["series"], "episode": script["episode"], "title": script["title"],
            "duration": duration, "fps": 30, "width": 1080, "height": 1920,
            "shots": rows, "lines": []}


class ValidationTests(unittest.TestCase):
    def test_lint_gate_rejects_ten_errors(self):
        from shortclip.external import lint_zero
        self.assertTrue(lint_zero("◇  0 error(s), 1 warning(s)"))
        self.assertFalse(lint_zero("◇  10 error(s), 0 warning(s)"))
        self.assertFalse(lint_zero("◇  20 error(s)"))
        self.assertFalse(lint_zero("no summary line at all"))

    def test_usage_is_one_object_that_sums_all_attempts(self):
        from shortclip.usage import merge_usage
        merged = merge_usage([
            {"input_tokens": 12, "output_tokens": 100, "cache_read_input_tokens": 5},
            {"input_tokens": 3, "output_tokens": 40, "cache_creation_input_tokens": 7},
        ])
        self.assertEqual(merged["output_tokens"], 140)
        self.assertEqual(merged["input_tokens"], 15)
        self.assertEqual(merged["cache_read_input_tokens"], 5)
        self.assertEqual(merged["cache_creation_input_tokens"], 7)
        self.assertEqual(merged["passes"], 2)
        self.assertEqual(merge_usage([])["output_tokens"], 0)

    def test_real_episode_contract(self):
        self.assertEqual(len(validate_script(REAL_SCRIPT, "zombie-school", 1)["lines"]), 18)
        self.assertEqual(len(validate_shots(REAL_SHOTS, REAL_SCRIPT)["shots"]), 18)

    def test_missing_beat_is_rejected(self):
        bad = dict(REAL_SCRIPT)
        bad["lines"] = [dict(x) for x in REAL_SCRIPT["lines"]]
        bad["lines"][0]["beat"] = "cold_open"
        with self.assertRaises(ValidationError):
            validate_script(bad, "zombie-school", 1)

    def test_title_card_over_three_seconds_is_rejected(self):
        bad = dict(REAL_SHOTS)
        bad["shots"] = [dict(x) for x in REAL_SHOTS["shots"]]
        bad["shots"][0]["end"] = 4.0
        with self.assertRaises(ValidationError):
            validate_shots(bad, REAL_SCRIPT)


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "db").mkdir()
        (self.root / "config").mkdir()
        shutil.copy2(ROOT / "db/schema.sql", self.root / "db/schema.sql")
        shutil.copy2(ROOT / "config/models.json", self.root / "config/models.json")
        self.calls = []
        self.pipe = Pipeline(self.root, self.root / "db/test.db", handlers=self.handlers())

    def tearDown(self):
        self.pipe.close()
        self.tmp.cleanup()

    def handlers(self):
        calls = self.calls
        def writer(job, episode, step, epdir, log):
            calls.append("writer")
            data = json.loads(json.dumps(REAL_SCRIPT))
            data["episode"] = episode["number"]
            (epdir / "script.json").write_text(json.dumps(data, ensure_ascii=False))
            self.pipe.con.execute("UPDATE episodes SET title=?,script_json=? WHERE id=?",
                                  (data["title"], json.dumps(data), episode["id"]))
            return {"title": data["title"], "title_options": data["title_options"]}
        def sound(job, episode, step, epdir, log):
            calls.append("sound")
            (epdir / "audio").mkdir(exist_ok=True)
            (epdir / "audio/mix.wav").write_bytes(b"mix")
            return {"ok": True}
        def director(job, episode, step, epdir, log):
            calls.append("director")
            script = json.loads((epdir / "script.json").read_text())
            shots = shots_for(script)
            (epdir / "shots.json").write_text(json.dumps(shots, ensure_ascii=False))
            (epdir / "renders" / f"{epdir.name}.srt").write_text("test")
            self.pipe.con.execute("UPDATE episodes SET shots_json=?,duration=60 WHERE id=?",
                                  (json.dumps(shots), episode["id"]))
            return {"shots": len(shots["shots"])}
        def animator(job, episode, step, epdir, log):
            calls.append("animator")
            (epdir / "composition").mkdir(exist_ok=True)
            (epdir / "composition/index.html").write_text("<html/>")
            (epdir / "composition/renders").mkdir(exist_ok=True)
            (epdir / "composition/renders/draft.mp4").write_bytes(b"draft")
            return {"cost_usd": 0.25}
        def editor(job, episode, step, epdir, log):
            calls.append("editor")
            (epdir / "renders/final.mp4").write_bytes(b"final")
            (epdir / "renders/web.mp4").write_bytes(b"web")
            qa = {"passed": True, "fails": [], "video": {"duration": 60.0},
                  "audio": {"lufs": -14.0, "true_peak": -2.0}}
            (epdir / "qa.json").write_text(json.dumps(qa))
            return {"duration": 60.0}
        return {"writer": writer, "sound": sound, "director": director,
                "animator": animator, "editor": editor}

    def create(self, run=True):
        return self.pipe.create("zombie-school", 1, run=run)

    def test_gate1_is_idempotent_and_gate2_finishes(self):
        state = self.create()
        self.assertEqual(state["job"]["status"], "waiting_gate")
        self.assertEqual(state["job"]["gate"], "gate1_script")
        self.assertEqual(self.calls, ["writer"])
        jid = state["job"]["id"]
        self.pipe.run(jid)
        self.assertEqual(self.calls, ["writer"])
        state = self.pipe.advance(jid, "approve")
        self.assertEqual(state["job"]["gate"], "gate2_final")
        self.assertEqual(self.calls, ["writer", "sound", "director", "animator", "editor"])
        self.assertTrue((self.root / "projects/zombie-school/ep1/renders/manifest.json").exists())
        state = self.pipe.advance(jid, "approve")
        self.assertEqual(state["job"]["status"], "done")
        self.assertEqual(state["episode"]["status"], "published-ready")
        self.pipe.run(jid)
        self.assertEqual(len(self.calls), 5)

    def test_reject_gate1_rewrites_only_writer(self):
        jid = self.create()["job"]["id"]
        state = self.pipe.advance(jid, "reject")
        self.assertEqual(state["job"]["gate"], "gate1_script")
        self.assertEqual(self.calls, ["writer", "writer"])
        writer_step = next(s for s in state["steps"] if s["role"] == "writer")
        self.assertEqual(writer_step["attempt"], 2)

    def test_reject_gate2_polishes_only_visual_tail(self):
        jid = self.create()["job"]["id"]
        self.pipe.advance(jid, "approve")
        self.calls.clear()
        state = self.pipe.advance(jid, "reject")
        self.assertEqual(self.calls, ["animator", "editor"])
        self.assertEqual(state["job"]["gate"], "gate2_final")

    def test_interrupted_step_resumes_without_restarting_job(self):
        state = self.create(run=False)
        jid = state["job"]["id"]
        self.pipe.con.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
        self.pipe.con.execute("UPDATE steps SET status='running',pid=NULL WHERE job_id=? AND role='writer'", (jid,))
        state = self.pipe.resume(jid)
        self.assertEqual(state["job"]["gate"], "gate1_script")
        self.assertEqual(self.calls, ["writer"])
        self.assertEqual(state["steps"][0]["attempt"], 1)

    def test_cancelled_job_leaves_the_queue_and_cannot_run(self):
        jid = self.create()["job"]["id"]
        state = self.pipe.cancel(jid)
        self.assertEqual(state["job"]["status"], "cancelled")
        self.assertEqual(self.pipe.status()["queue"], [])
        with self.assertRaises(Exception):
            self.pipe.advance(jid, "approve")
        self.assertEqual(self.calls, ["writer"])

    def test_empty_database_is_initialised_and_migrated_twice(self):
        con = connect(self.root / "db/migration.db")
        init_db(con)
        init_db(con)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(jobs)")}
        self.assertIn("params_json", cols)
        con.close()

    def test_legacy_list_usage_is_repaired_by_init_db(self):
        jid = self.create(run=False)["job"]["id"]
        sid = self.pipe.con.execute("SELECT id FROM steps WHERE job_id=? AND role='writer'", (jid,)).fetchone()[0]
        self.pipe.con.execute("UPDATE steps SET usage_json=? WHERE id=?",
                              (json.dumps([{"output_tokens": 70}, {"output_tokens": 5}]), sid))
        init_db(self.pipe.con)
        row = self.pipe.con.execute("SELECT usage_json FROM steps WHERE id=?", (sid,)).fetchone()
        self.assertEqual(json.loads(row["usage_json"])["output_tokens"], 75)
        self.assertEqual(self.pipe.con.execute("SELECT output_tokens FROM v_episode_cost").fetchone()[0], 75)

    def test_dashboard_token_count_matches_step_usage(self):
        jid = self.create()["job"]["id"]
        sid = self.pipe.con.execute("SELECT id FROM steps WHERE job_id=? AND role='writer'", (jid,)).fetchone()[0]
        self.pipe._charge(sid, "m", {"usage": {"output_tokens": 100}, "total_cost_usd": 0.5}, "a.log")
        self.pipe._charge(sid, "m", {"usage": {"output_tokens": 40}, "total_cost_usd": 0.25}, "b.log")
        self.pipe._charge(sid, "m", {"usage": {"output_tokens": 40}, "total_cost_usd": 0.25}, "b.log")  # same pass twice
        row = self.pipe.con.execute("SELECT cost_usd,output_tokens FROM v_episode_cost").fetchone()
        self.assertAlmostEqual(row["cost_usd"], 0.75)
        self.assertEqual(row["output_tokens"], 140)

    # ---- edit any stage ----

    def _epdir(self):
        return self.root / "projects/zombie-school/ep1"

    def _to_gate2(self):
        jid = self.create()["job"]["id"]
        self.pipe.advance(jid, "approve")
        self.calls.clear()
        return jid

    def test_cancelled_episode_can_be_redone_from_the_picture(self):
        jid = self._to_gate2()
        self.pipe.cancel(jid)
        (self._epdir() / "composition/index.html").write_text("<html/>")
        state = self.pipe.redo(jid, "animator", "ช็อต 5 มืดไป")
        self.assertEqual(self.calls, ["animator", "editor"])           # sound/writer untouched
        self.assertEqual(state["job"]["status"], "waiting_gate")
        self.assertEqual(state["job"]["gate"], "gate2_final")
        anim = next(s for s in state["steps"] if s["role"] == "animator")
        self.assertEqual(json.loads(anim["input_json"])["revision"], "ช็อต 5 มืดไป")

    def test_redo_picture_needs_a_reason_unless_fresh(self):
        jid = self._to_gate2()
        with self.assertRaises(Exception):
            self.pipe.redo(jid, "animator", "")
        self.pipe.redo(jid, "animator", fresh=True)
        self.assertEqual(self.calls, ["animator", "editor"])
        self.assertTrue(list((self._epdir() / "backup").glob("index.redraw-*.html")))

    def test_redo_voice_only_selected_beats_keeps_the_others(self):
        jid = self._to_gate2()
        beats = self._epdir() / "audio/beats"
        beats.mkdir(parents=True, exist_ok=True)
        for b in ("hook", "setup", "escalate", "twist", "cliff"):
            (beats / f"{b}.wav").write_bytes(b"x")
        self.pipe.redo(jid, "sound", beats=["twist"])
        self.assertEqual(self.calls, ["sound", "director", "animator", "editor"])
        self.assertFalse((beats / "twist.wav").exists())
        self.assertTrue((beats / "hook.wav").exists())
        self.assertTrue((self._epdir() / "backup/beats-redo/twist.wav").exists())

    def test_redo_voice_retime_only_keeps_every_take(self):
        jid = self._to_gate2()
        beats = self._epdir() / "audio/beats"
        beats.mkdir(parents=True, exist_ok=True)
        (beats / "hook.wav").write_bytes(b"x")
        self.pipe.redo(jid, "sound", beats=[])
        self.assertTrue((beats / "hook.wav").exists())

    def test_redo_writer_goes_back_to_gate1_with_the_reason(self):
        jid = self._to_gate2()
        state = self.pipe.redo(jid, "writer", "ให้หักมุมแรงขึ้น")
        self.assertEqual(self.calls, ["writer"])
        self.assertEqual(state["job"]["gate"], "gate1_script")

    def test_edit_script_text_only_revoices_changed_beat_and_keeps_picture(self):
        jid = self._to_gate2()
        ep = self._epdir()
        (ep / "composition/index.html").write_text("<html/>")
        beats = ep / "audio/beats"
        beats.mkdir(parents=True, exist_ok=True)
        for b in ("hook", "setup", "escalate", "twist", "cliff"):
            (beats / f"{b}.wav").write_bytes(b"x")
        sc = json.loads((ep / "script.json").read_text())
        target = next(i for i, l in enumerate(sc["lines"]) if l["beat"] == "twist")
        sc["lines"][target]["text"] = "ประโยคใหม่ที่ผู้ใช้แก้เองสิบวินาที"
        out = self.pipe.edit_script(jid, sc, run=False)
        self.assertTrue(out["same_layout"])
        self.assertEqual(out["changed_beats"], ["twist"])
        self.assertEqual(out["rerun"], "sound")
        self.assertFalse((beats / "twist.wav").exists())
        self.assertTrue((beats / "hook.wav").exists())
        self.assertTrue((ep / "composition/index.html").exists())
        self.assertIn("ประโยคใหม่", json.loads((ep / "script.json").read_text())["lines"][target]["text"])
        steps = {s["role"]: s for s in out["steps"]}
        self.assertEqual(steps["writer"]["status"], "done")
        self.assertEqual(steps["sound"]["status"], "queued")

    def test_edit_script_new_line_needs_a_new_picture(self):
        jid = self._to_gate2()
        ep = self._epdir()
        (ep / "composition/index.html").write_text("<html/>")
        sc = json.loads((ep / "script.json").read_text())
        sc["lines"].insert(5, {"id": "l05x1", "beat": sc["lines"][4]["beat"], "text": "บรรทัดที่เพิ่มเข้ามาใหม่", "on_screen": ""})
        out = self.pipe.edit_script(jid, sc, run=False)
        self.assertFalse(out["same_layout"])
        self.assertEqual(out["rerun"], "sound+animator")
        self.assertFalse((ep / "composition/index.html").exists())

    def test_edit_script_at_gate1_only_saves(self):
        jid = self.create()["job"]["id"]
        sc = json.loads((self._epdir() / "script.json").read_text())
        sc["lines"][3]["text"] = "แก้ก่อนอนุมัติบทสามนาที"
        out = self.pipe.edit_script(jid, sc)
        self.assertEqual(out["job"]["gate"], "gate1_script")
        self.assertEqual(self.calls, ["writer"])

    def test_edit_script_rejects_an_invalid_script(self):
        jid = self.create()["job"]["id"]
        sc = json.loads((self._epdir() / "script.json").read_text())
        sc["lines"] = sc["lines"][:5]
        with self.assertRaises(ValidationError):
            self.pipe.edit_script(jid, sc)


if __name__ == "__main__":
    unittest.main()
