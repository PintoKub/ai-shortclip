"""Karaoke subtitles, word alignment and dashboard auth/actions (no network, no Claude)."""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

from shortclip.validate import verify_composition
from test_contracts import HTML, SHOTS

ROOT = Path(__file__).resolve().parent.parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


karaoke = load(ROOT / "tools/karaoke.py", "karaoke")
tts = load(ROOT / "tools/tts_natural.py", "tts_natural")


class KaraokeTests(unittest.TestCase):
    def shots(self):
        data = json.loads(json.dumps(SHOTS))
        data["shots"][1]["subtitle"] = "สอง สาม"
        data["shots"][1]["words"] = [{"t": "สอง", "s": 2.6, "e": 3.0}, {"t": "สาม", "s": 3.1, "e": 4.8}]
        data["shots"][0]["words"] = [{"t": "หนึ่ง", "s": 0.4, "e": 2.4}]
        return data

    def html(self):
        return HTML.replace(">สอง<", ">สอง สาม<").replace("</div><script>", "</div></body><script>") \
            .replace('<div id="root"', '<head></head><body><div id="root"')

    def test_wraps_words_and_keeps_contract(self):
        shots = self.shots()
        import tools_retime  # noqa: F401  (registered in setUpModule)
        html = sys.modules["tools_retime"].retime(self.html(), shots)
        out, count = karaoke.apply(html, shots)
        self.assertEqual(count, 2)
        self.assertIn('data-ks="3.100"', out)
        self.assertIn('<span class="kp">', out)
        self.assertEqual(verify_composition(out, shots), [])

    def test_idempotent_and_removable(self):
        shots = self.shots()
        once, _ = karaoke.apply(self.html(), shots)
        twice, _ = karaoke.apply(once, shots)
        self.assertEqual(once, twice)
        self.assertEqual(once.count('id="karaoke-fx"'), 1)
        off, count = karaoke.apply(once, shots, enabled=False)
        self.assertEqual(count, 0)
        self.assertNotIn("karaoke-fx", off)
        self.assertNotIn('class="kw"', off)

    def test_words_that_do_not_match_text_leave_plain_subtitle(self):
        shots = self.shots()
        shots["shots"][1]["words"] = [{"t": "ผิด", "s": 2.6, "e": 3.0}]
        out, count = karaoke.apply(self.html(), shots)
        self.assertEqual(count, 1)
        self.assertIn(">สอง สาม<", out)


class WordAlignTests(unittest.TestCase):
    def test_words_are_monotonic_and_inside_line(self):
        lines = [{"id": "l01", "text": "กินข้าว กับแกง"}, {"id": "l02", "text": "แล้วนอน"}]
        text = tts.PAUSE_MARK.join(x["text"] for x in lines)
        chars = [c for c in tts.norm(text)]
        words = [{"w": c, "s": round(i * 0.1, 3), "e": round(i * 0.1 + 0.1, 3)} for i, c in enumerate(chars)]
        spans = tts.align_lines(text, lines, words)
        toks = [["กิน", "ข้าว", "กับ", "แกง"], ["แล้ว", "นอน"]]
        out = tts.align_words(text, lines, words, toks, spans)
        for (s, e), ws in zip(spans, out):
            starts = [w["s"] for w in ws]
            self.assertEqual(starts, sorted(starts))
            self.assertGreaterEqual(ws[0]["s"], s)
            self.assertLessEqual(ws[-1]["s"], e)
        self.assertEqual([w["t"] for w in out[0]], toks[0])

    def test_unmatched_tokens_fall_back_to_proportional(self):
        out = tts.proportional_words(["ab", "cdef"], 1.0, 4.0)
        self.assertAlmostEqual(out[0]["s"], 1.0)
        self.assertAlmostEqual(out[1]["s"], 2.0)
        self.assertAlmostEqual(out[1]["e"], 4.0)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        os.environ["SHORTCLIP_DB"] = str(tmp / "t.db")
        os.environ["SHORTCLIP_DASH_PASS"] = str(tmp / "pass")
        os.environ["SHORTCLIP_DASH_DRYRUN"] = "1"
        os.environ["SHORTCLIP_SECRETS"] = str(tmp / "secrets")
        (tmp / "pass").write_text("correct horse\n")
        from shortclip.pipeline import Pipeline
        pipe = Pipeline(ROOT, tmp / "t.db")
        pipe.con.execute("INSERT INTO series(slug,title) VALUES('s','s')")
        pipe.con.execute("INSERT INTO episodes(series_id,number,title) VALUES(1,1,'EP.1')")
        pipe.con.execute("INSERT INTO jobs(episode_id,kind,status,gate) VALUES(1,'new_episode','waiting_gate','gate1_script')")
        pipe.close()
        cls.server_mod = load(ROOT / "dashboard/server.py", "dash_server")
        cls.server_mod.SECRET_FILE = tmp / "secret"
        from http.server import ThreadingHTTPServer
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.server_mod.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for k in ("SHORTCLIP_DB", "SHORTCLIP_DASH_PASS", "SHORTCLIP_DASH_DRYRUN", "SHORTCLIP_SECRETS"):
            os.environ.pop(k, None)
        cls.tmp.cleanup()

    def opener(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        jar = CookieJar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect), jar

    def call(self, op, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        try:
            with op.open(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def login(self, op, pw):
        return self.call(op, "/login", f"password={pw}".encode(),
                         {"Content-Type": "application/x-www-form-urlencoded"})

    def test_api_requires_login(self):
        op, _ = self.opener()
        self.assertEqual(self.call(op, "/api/jobs")[0], 401)
        self.assertEqual(self.call(op, "/")[0], 303)
        self.assertEqual(self.call(op, "/api/health")[0], 200)

    def test_login_then_gate_action(self):
        op, jar = self.opener()
        self.assertEqual(self.login(op, "wrong")[0], 303)
        self.assertFalse(any(c.name == "sc_session" for c in jar))
        # urllib refuses Secure cookies on http; copy the token by hand for the test
        token = self.server_mod.make_token()
        hdr = {"Cookie": f"sc_session={token}"}
        self.assertEqual(self.call(op, "/api/jobs", headers=hdr)[0], 200)
        status, _ = self.call(op, "/api/job/1/approve", b"{}", {**hdr, "Content-Type": "application/json"})
        self.assertEqual(status, 403)  # missing X-ShortClip header
        status, body = self.call(op, "/api/job/1/approve", b"{}",
                                 {**hdr, "Content-Type": "application/json", "X-ShortClip": "1"})
        self.assertEqual(status, 200, body)
        self.assertIn("dry-run", body)
        status, _ = self.call(op, "/api/job/1/resume", b"{}",
                              {**hdr, "Content-Type": "application/json", "X-ShortClip": "1"})
        self.assertEqual(status, 409)
        status, _ = self.call(op, "/api/new", json.dumps({"series": "bad slug", "ep": 2}).encode(),
                              {**hdr, "Content-Type": "application/json", "X-ShortClip": "1"})
        self.assertEqual(status, 400)
        status, _ = self.call(op, "/media?path=../../etc/passwd", headers=hdr)
        self.assertEqual(status, 403)

    def test_voices_series_and_files_endpoints(self):
        op, _ = self.opener()
        hdr = {"Cookie": f"sc_session={self.server_mod.make_token()}"}
        status, body = self.call(op, "/api/voices", headers=hdr)
        self.assertEqual(status, 200, body)
        self.assertIn("th-TH-PremwadeeNeural", body)
        self.assertNotIn("f_young_warm", body)          # Wayu is hidden unless installed (non-commercial licence)
        with tempfile.TemporaryDirectory() as wayu:
            os.environ["WAYU_DIR"] = wayu
            try:
                status, body = self.call(op, "/api/voices", headers=hdr)
            finally:
                del os.environ["WAYU_DIR"]
        self.assertIn("f_young_warm", body)
        self.assertEqual(self.call(op, "/api/series/s", headers=hdr)[0], 200)
        self.assertEqual(self.call(op, "/api/episode/1/files", headers=hdr)[0], 200)
        status, body = self.call(op, "/api/series/s", json.dumps({"rules": {"story": ["x"]},
                                 "voice": {"engine": "wayu", "voice": "f_young_warm", "speed": 1.25}}).encode(),
                                 {**hdr, "Content-Type": "application/json", "X-ShortClip": "1"})
        self.assertEqual(status, 200, body)
        status, _ = self.call(op, "/api/job/1/voice", json.dumps({"voice": {"engine": "wayu", "voice": "bad"}}).encode(),
                              {**hdr, "Content-Type": "application/json", "X-ShortClip": "1"})
        self.assertEqual(status, 400)

    def test_forged_or_expired_token_rejected(self):
        m = self.server_mod
        self.assertFalse(m.token_ok("123.abc"))
        self.assertFalse(m.token_ok(m.make_token(now=time.time() - 30 * 86400)))
        self.assertTrue(m.token_ok(m.make_token()))


def setUpModule():
    sys.modules["tools_retime"] = load(ROOT / "tools/retime.py", "tools_retime")


if __name__ == "__main__":
    unittest.main()


class RateLimitTests(unittest.TestCase):
    def test_429_waits_and_retries_then_succeeds(self):
        import shortclip.external as ext
        tmp = tempfile.TemporaryDirectory()
        log = Path(tmp.name) / "c.log"
        calls, slept = [], []
        bad = json.dumps({"subtype": "success", "usage": {}, "is_error": True, "api_error_status": 429,
                          "result": "API Error (429) rate limit (reset after 2m 5s)"})
        good = json.dumps({"subtype": "success", "usage": {"output_tokens": 1}, "is_error": False,
                           "total_cost_usd": 0.1, "result": "ok"})

        def fake_run(cmd, *, cwd, log_path, timeout, env=None):
            calls.append(1)
            log_path.write_text(bad if len(calls) == 1 else good)
            if len(calls) == 1:
                raise ext.StepError("claude exited 1")
            return {}
        old_run, old_env = ext.run_process, ext.process_env
        ext.run_process, ext.process_env = fake_run, lambda require_key=True: {}
        try:
            out = ext.claude_json("p", model="m", cwd=Path(tmp.name), log_path=log, max_turns=1,
                                  sleep=slept.append)
        finally:
            ext.run_process, ext.process_env = old_run, old_env
            tmp.cleanup()
        self.assertEqual(out["result"], "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(slept, [185])

    def test_other_errors_are_not_retried(self):
        import shortclip.external as ext
        tmp = tempfile.TemporaryDirectory()
        log = Path(tmp.name) / "c.log"
        log.write_text("no json here")
        self.assertIsNone(ext.rate_limit_wait(log))
        tmp.cleanup()


class VoiceAndRulesTests(unittest.TestCase):
    def setUp(self):
        from test_system import StateMachineTests  # reuse its fake handlers
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("db", "config", "agents"):
            (self.root / d).mkdir()
        import shutil
        shutil.copy2(ROOT / "db/schema.sql", self.root / "db/schema.sql")
        shutil.copy2(ROOT / "config/models.json", self.root / "config/models.json")
        from shortclip.pipeline import Pipeline
        self.calls = []
        helper = StateMachineTests()
        helper.calls, helper.root = self.calls, self.root
        self.pipe = Pipeline(self.root, self.root / "db/t.db")
        helper.pipe = self.pipe
        self.pipe.handlers = helper.handlers()

    def tearDown(self):
        self.pipe.close()
        self.tmp.cleanup()

    def test_series_rules_and_voice_flow_into_new_episode(self):
        self.pipe.set_series("zombie-school", rules={"story": ["ตัวเอกชื่อต้น", ""], "visual": "ทุกช็อตเห็นตัวละคร\n"},
                             voice={"engine": "wayu", "voice": "f_young_warm", "speed": 1.25})
        s = self.pipe.series_settings("zombie-school")
        self.assertEqual(s["rules"], {"story": ["ตัวเอกชื่อต้น"], "visual": ["ทุกช็อตเห็นตัวละคร"]})
        state = self.pipe.create("zombie-school", 1, rules={"story": ["มีมุกตลก"]})
        epdir = Path(json.loads(state["job"]["params_json"])["epdir"])
        self.assertEqual(json.loads((epdir / "voice.json").read_text())["engine"], "wayu")
        self.assertEqual(self.pipe._rules("zombie-school", json.loads(state["job"]["params_json"]), "story"),
                         ["ตัวเอกชื่อต้น", "มีมุกตลก"])

    def test_bad_voice_is_rejected(self):
        from shortclip.external import StepError
        with self.assertRaises(StepError):
            self.pipe.set_series("zombie-school", voice={"engine": "wayu", "voice": "nope"})
        with self.assertRaises(StepError):
            self.pipe.create("zombie-school", 1, voice={"engine": "edge", "voice": "th-TH-NiwatNeural", "speed": 5})

    def test_set_voice_after_gate2_redoes_sound_onward_without_writer(self):
        jid = self.pipe.create("zombie-school", 1)["job"]["id"]
        self.pipe.advance(jid, "approve")
        self.calls.clear()
        state = self.pipe.set_voice(jid, {"engine": "wayu", "voice": "f_young_warm", "speed": 1.25})
        self.assertEqual(self.calls, ["sound", "director", "animator", "editor"])
        self.assertEqual(state["job"]["gate"], "gate2_final")

    def test_set_voice_before_sound_only_records_it(self):
        jid = self.pipe.create("zombie-school", 1)["job"]["id"]
        self.calls.clear()
        state = self.pipe.set_voice(jid, {"engine": "edge", "voice": "th-TH-NiwatNeural", "speed": 1})
        self.assertEqual(self.calls, [])
        self.assertEqual(state["job"]["gate"], "gate1_script")

    def test_writer_prompt_contains_rules(self):
        from shortclip.briefs import writer_prompt, animator_brief
        p = writer_prompt(ROOT, self.root, "zombie-school", 3, "countdown", "", rules=["ห้ามฉากเลือด"], target_seconds=55)
        self.assertIn("ห้ามฉากเลือด", p)
        self.assertIn("55", p)
        shots = json.loads((ROOT / "examples/zombie-school/ep1v2/shots.json").read_text())
        self.assertIn("ทุกช็อตเห็นตัวละคร", animator_brief(ROOT, self.root, shots, None, rules=["ทุกช็อตเห็นตัวละคร"]))


class VoiceEngineTests(unittest.TestCase):
    def test_wayu_hook_plays_at_chosen_speed_and_later_beats_slow_down(self):
        cfg = tts.normalize_voice({"engine": "wayu", "voice": "f_young_warm", "speed": 1.25})
        self.assertAlmostEqual(tts.beat_speed(cfg, "hook"), 1.25)
        self.assertLess(tts.beat_speed(cfg, "cliff"), tts.beat_speed(cfg, "escalate"))
        self.assertEqual(tts.beat_speed(tts.normalize_voice(None), "hook"), "+13%")

    def test_unknown_voice_falls_back_to_engine_default(self):
        self.assertEqual(tts.normalize_voice({"engine": "wayu", "voice": "x"})["voice"], "f_young_warm")


class RevoiceDoesNotPayTwiceTests(unittest.TestCase):
    def test_second_revoice_finds_claude_log_two_attempts_back(self):
        from shortclip.pipeline import Pipeline
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "db").mkdir(); (root / "config").mkdir()
        import shutil
        shutil.copy2(ROOT / "db/schema.sql", root / "db/schema.sql")
        shutil.copy2(ROOT / "config/models.json", root / "config/models.json")
        pipe = Pipeline(root, root / "db/t.db")
        logs = root / "logs"; logs.mkdir()
        ok = json.dumps({"subtype": "success", "is_error": False, "usage": {"output_tokens": 5}, "total_cost_usd": 1.0})
        (logs / "04-animator-attempt2.log").write_text("COMMAND: x\n\n" + ok)
        (logs / "04-animator-attempt3-retime.log").write_text("retime only")
        pipe.con.execute("INSERT INTO jobs(id,kind,status) VALUES(1,'new_episode','running')")
        pipe.con.execute("INSERT INTO steps(id,job_id,role,status,attempt,position,log_path) VALUES(9,1,'animator','running',3,4,?)",
                         (str(logs / "04-animator-attempt3.log"),))
        step = dict(pipe.con.execute("SELECT * FROM steps WHERE id=9").fetchone())
        self.assertTrue(pipe._paid_before(step, "m"))
        self.assertTrue(pipe._paid_before(step, "m"))
        cost = pipe.con.execute("SELECT cost_usd FROM steps WHERE id=9").fetchone()[0]
        self.assertAlmostEqual(cost, 1.0)  # charged once even when found twice
        pipe.close(); tmp.cleanup()


def _bible_doc(cache_url, episodes=2, start=1):
    return {"title": "ผีเรียนมหาลัย", "slug": "ghost-uni", "logline": "ผีมาเรียนวิศวะ",
            "world": {"setting": "กรุงเทพฯ", "rules": ["ผีถูกเห็นได้ตอนกลางคืน"]},
            "characters": [{"id": "ch1", "name": "นาก", "role": "ผี", "look": "ห่มสไบ ผมยาว",
                            "abilities": ["ยืดแขน"], "limits": ["ข้ามน้ำไม่ได้"], "goal": "เรียนจบ"}],
            "lore": [{"id": "lr1", "kind": "legend", "name": "แม่นาก", "fact": "ตำนานพระโขนง",
                      "source_url": cache_url, "twist": "มาเรียนวิศวะ", "used_in": [1]},
                     {"id": "lr2", "kind": "place", "name": "ปลอม", "fact": "x",
                      "source_url": "https://example.com/made-up", "twist": "y"}],
            "episodes": [{"number": start + i, "title": f"ตอน {i}", "hook_type": "forbidden", "hook": "ห้ามมองท่าน้ำ",
                          "summary": "s", "characters": ["ch1", "zz"], "lore": ["lr1", "lr2"], "cliffhanger": "c"}
                         for i in range(episodes)],
            "story_rules": ["ตัวเอกพูดน้อย"], "visual_rules": ["กลางคืนทั้งตอน"]}


class PlanningTests(unittest.TestCase):
    URL = "https://th.wikipedia.org/wiki/%E0%B9%81%E0%B8%A1%E0%B9%88%E0%B8%99%E0%B8%B2%E0%B8%81"

    def setUp(self):
        import shutil
        from shortclip import db as dbm
        from shortclip.planning import Planner
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("db", "config", "agents"):
            (self.root / d).mkdir()
        shutil.copy2(ROOT / "db/schema.sql", self.root / "db/schema.sql")
        shutil.copy2(ROOT / "config/models.json", self.root / "config/models.json")
        shutil.copy2(ROOT / "agents/planner.md", self.root / "agents/planner.md")
        self.con = dbm.connect(self.root / "db/t.db")
        dbm.init_db(self.con)
        self.pl = Planner(self.root, self.con)
        self.prompts = []
        import shortclip.planning as planning
        self.planning = planning
        self._orig = planning.claude_json
        planning.claude_json = self.fake_claude

    def tearDown(self):
        self.planning.claude_json = self._orig
        self.con.close()
        self.tmp.cleanup()

    def fake_claude(self, prompt, *, cwd, log_path, **kw):
        prompt = prompt() if callable(prompt) else prompt
        self.prompts.append(prompt)
        cwd = Path(cwd)
        log_path.write_text("x")
        (cwd / "sources.jsonl").write_text(json.dumps({"title": "แม่นาก", "url": self.URL}) + "\n")
        if "งานนี้: งาน A" in prompt or "งาน A" in prompt and "งานนี้" not in prompt:
            (cwd / "concepts.json").write_text(json.dumps({"concepts": [
                {"title": f"เรื่อง {i}", "logline": "l", "hook_3s": "h", "why_watch": "w", "season_arc": "a",
                 "episode_seeds": ["e1", "e2"]} for i in range(self.count)]}, ensure_ascii=False))
        elif "งานนี้: งาน B" in prompt:
            (cwd / "summary.json").write_text(json.dumps({"title": "ผีเรียนมหาลัย", "slug": "Ghost Uni!", "logline": "l",
                                                          "premise": "p", "season_arc": "a", "core_mystery": "m"}))
        elif "งานนี้: งาน C" in prompt:
            (cwd / "bible.json").write_text(json.dumps(_bible_doc(self.URL, 2), ensure_ascii=False))
        return {"total_cost_usd": 0.1, "usage": {"input_tokens": 5}}

    def test_full_flow_idea_to_series_with_custom_count(self):
        self.count = 4
        pid = self.pl.create("ผีมาเรียน", {"count": 4, "tone": "ตลก"})
        p = self.pl.concepts(pid)
        self.assertEqual(p["status"], "concepts_ready")
        self.assertEqual(len(p["concepts"]), 4)
        self.assertIn("ตลก", self.prompts[-1])
        p = self.pl.choose(pid, {"id": "c2", "title": "ชื่อที่ผู้ใช้แก้"}, "อยากให้ตลกขึ้น")
        self.assertEqual(p["chosen"]["title"], "ชื่อที่ผู้ใช้แก้")
        self.assertIn("ชื่อที่ผู้ใช้แก้", self.prompts[-1])
        self.assertEqual(p["summary"]["slug"], "ghost-uni")
        p = self.pl.save_summary(pid, {"title": "ชื่อใหม่"})
        self.assertEqual(p["summary"]["title"], "ชื่อใหม่")
        p = self.pl.bible(pid, {"episodes": 2, "characters": "นากเป็นผีใจดี"})
        self.assertEqual(p["status"], "bible_ready")
        self.assertIn("นากเป็นผีใจดี", self.prompts[-1])
        b = p["bible"]
        self.assertEqual([x["id"] for x in b["lore"]], ["lr1"], "made-up source must be dropped")
        self.assertTrue(any("ปลอม" in w for w in p["warnings"]))
        self.assertEqual(b["episodes"][0]["lore"], ["lr1"])
        self.assertEqual(b["episodes"][0]["characters"], ["ch1"])
        p = self.pl.approve(pid)
        self.assertEqual(p["status"], "approved")
        row = self.con.execute("SELECT * FROM series WHERE slug='ghost-uni'").fetchone()
        self.assertEqual(json.loads(row["rules_json"])["rules"]["visual"], ["กลางคืนทั้งตอน"])
        self.assertTrue((self.root / "projects/ghost-uni/bible.json").exists())
        self.assertAlmostEqual(p["cost_usd"], 0.3)  # concepts + summary + bible, each once

    def test_wrong_count_is_repaired_then_fails_cleanly(self):
        self.count = 2
        pid = self.pl.create("x", {"count": 3})
        with self.assertRaises(Exception):
            self.pl.concepts(pid)
        p = self.pl.get(pid)
        self.assertEqual(p["status"], "failed")
        self.assertIn("exactly 3", p["error"])
        self.assertIsNone(p["busy_step"])

    def test_writer_and_animator_get_the_bible(self):
        from shortclip.planning import bible_for_animator, bible_for_writer, validate_bible
        cache = self.root / "s.jsonl"
        cache.write_text(json.dumps({"url": self.URL}) + "\n")
        bible, _ = validate_bible(_bible_doc(self.URL, 3, 4), 3, 4, cache)
        w = bible_for_writer(bible, 5)
        self.assertIn("EP.5", w)
        self.assertIn("ห้ามมองท่าน้ำ", w)
        self.assertIn("ข้ามน้ำไม่ได้", w)
        self.assertIn("ตำนานพระโขนง", w)
        self.assertIn("ห่มสไบ", bible_for_animator(bible, 5))
        self.assertEqual(bible_for_writer(None, 1), "")
        with self.assertRaises(ValueError):
            validate_bible(_bible_doc(self.URL, 2, 4), 3, 4, cache)


class PlanDashboardTests(DashboardTests):
    def test_agents_and_plan_endpoints(self):
        op, _ = self.opener()
        hdr = {"Cookie": f"sc_session={self.server_mod.make_token()}"}
        status, body = self.call(op, "/api/agents", headers=hdr)
        self.assertEqual(status, 200, body)
        roles = [a["role"] for a in json.loads(body)["agents"]]
        self.assertEqual(roles[:2], ["planner", "writer"])
        self.assertEqual(self.call(op, "/api/plans", headers=hdr)[0], 200)
        self.assertEqual(self.call(op, "/api/plan/999", headers=hdr)[0], 404)
        post = {**hdr, "Content-Type": "application/json", "X-ShortClip": "1"}
        self.assertEqual(self.call(op, "/api/plan", b'{"idea":""}', post)[0], 400)
        status, body = self.call(op, "/api/plan", json.dumps({"idea": "ผี", "count": 9}).encode(), post)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["dry_run"]["count"], 5)
        self.assertEqual(self.call(op, "/app.js", headers=hdr)[0], 200)
        status, body = self.call(op, "/api/models", headers=hdr)
        self.assertEqual(status, 200, body)
        self.assertIn("planner", json.loads(body)["roles"])
        self.assertEqual(self.call(op, "/api/plan", json.dumps({"idea": "ผี", "model": "gpt-9"}).encode(), post)[0], 400)
        self.assertEqual(self.call(op, "/api/models", json.dumps({"roles": {"writer": "gpt-9"}}).encode(), post)[0], 400)
        self.assertNotIn("const $=", self.call(self.opener()[0], "/app.js")[1], "app.js must need login")


class QuotaWaitMarkerTests(unittest.TestCase):
    def test_marker_exists_only_while_sleeping(self):
        import shortclip.external as ext
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "bible-1.log"
        seen, calls = [], []
        orig_run, orig_wait = ext.run_process, ext.rate_limit_wait
        def run(cmd, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise ext.StepError("429")
            log.write_text('{"subtype":"success","usage":{},"total_cost_usd":0.1}')
        ext.run_process, ext.rate_limit_wait = run, lambda p: 120
        self.addCleanup(setattr, ext, "run_process", orig_run)
        self.addCleanup(setattr, ext, "rate_limit_wait", orig_wait)
        ext.claude_json("p", model="m", cwd=Path(tmp.name), log_path=log, max_turns=1,
                        sleep=lambda s: seen.append(log.with_suffix(".wait").exists()))
        self.assertEqual(seen, [True])
        self.assertFalse(log.with_suffix(".wait").exists())


class MergePartsTests(unittest.TestCase):
    def test_parts_merge_in_numeric_order(self):
        mp = load(ROOT / "tools/merge_parts.py", "merge_parts")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "bible.part-1.json").write_text(json.dumps({"title": "t", "characters": [1]}))
        (d / "bible.part-10.json").write_text(json.dumps({"episodes": [3]}))
        (d / "bible.part-2.json").write_text(json.dumps({"episodes": [1, 2]}))
        info = mp.merge(d / "bible")
        self.assertEqual(info["parts"], 3)
        self.assertEqual(json.loads((d / "bible.json").read_text())["episodes"], [1, 2, 3])
        (d / "bible.part-3.json").write_text("{bad")
        with self.assertRaises(SystemExit):
            mp.merge(d / "bible")


class BibleResumeTests(PlanningTests):
    def test_retry_prompt_lists_parts_already_written(self):
        from shortclip.planning import existing_parts
        d = self.pl.dir(1)
        (d / "bible.part-1.json").write_text('{"title": "t"}')
        (d / "bible.part-2.json").write_text('{"characters": [1, 2]}')
        (d / "bible.part-4.json").write_text('{"episodes": [{"number": 1}, {"number": 2}]}')
        (d / "bible.part-5.json").write_text('{"episo')  # cut mid-write
        self.assertEqual(existing_parts(d), [("bible.part-1.json", "ข้อมูลหลัก"), ("bible.part-2.json", "characters 2"),
                                             ("bible.part-4.json", "EP.1,2")])
        self.assertFalse((d / "bible.part-5.json").exists())

    def test_same_request_keeps_parts_other_request_clears_them(self):
        self.count = 3
        pid = self.pl.create("x", {})
        self.pl.concepts(pid)
        self.pl.choose(pid, {"id": "c1"})
        seen = self.prompts
        d = self.pl.dir(pid)
        (d / "bible.parts-opts.json").write_text(json.dumps({"episodes": 2, "start_episode": 1, "characters": "",
                                                               "anchors": "", "notes": ""}))
        (d / "bible.part-2.json").write_text('{"characters": [1]}')
        self.pl.bible(pid, {"episodes": 2})
        self.assertIn("ทำต่อจากรอบก่อน", seen[-1])
        self.assertIn("characters 1", seen[-1])
        (d / "bible.part-2.json").write_text('{"characters": [1]}')
        self.pl.bible(pid, {"episodes": 2, "characters": "อื่น"})
        self.assertNotIn("ทำต่อจากรอบก่อน", seen[-1])
        self.assertFalse((d / "bible.part-2.json").exists())


class ModelChoiceTests(PlanningTests):
    def test_plan_model_default_override_and_rejects_unknown(self):
        self.count = 3
        seen = []
        orig = self.fake_claude
        def fake(prompt, *, model, **kw):
            seen.append(model)
            return orig(prompt, **kw)
        self.planning.claude_json = fake
        pid = self.pl.create("x", {})
        self.assertEqual(self.pl.get(pid)["options"]["model"], self.pl.model)
        self.pl.concepts(pid)
        self.pl.set_model(pid, "haiku")
        self.pl.choose(pid, {"id": "c1"})
        self.assertEqual(seen, [self.pl.model, "haiku"])
        with self.assertRaises(ValueError):
            self.pl.set_model(pid, "gpt-9")
        with self.assertRaises(ValueError):
            self.pl.create("x", {"model": "gpt-9"})

    def test_set_models_only_known_roles_and_choices(self):
        from shortclip.external import load_models, set_models
        m = set_models(self.root, {"writer": "opus"})
        self.assertEqual(load_models(self.root)["writer"], "opus")
        for bad in ({"writer": "nope"}, {"janitor": "opus"}):
            with self.assertRaises(ValueError):
                set_models(self.root, bad)
        self.assertIn("choices", m)


class EpisodeModelTests(VoiceAndRulesTests):
    def test_episode_models_saved_and_validated(self):
        from shortclip.external import StepError
        r = self.pipe.create("s", 1, hook="countdown", idea="x", run=False, models={"animator": "opus"})
        job = self.pipe.con.execute("SELECT params_json FROM jobs ORDER BY id DESC").fetchone()
        params = json.loads(job[0])
        self.assertEqual(params["models"], {"animator": "opus"})
        self.assertEqual(self.pipe._model(params, "animator"), "opus")
        self.assertEqual(self.pipe._model(params, "writer"), self.pipe.models["writer"])
        with self.assertRaises(StepError):
            self.pipe.create("s", 2, hook="countdown", idea="x", run=False, models={"animator": "nope"})


class PublishDashboardTests(DashboardTests):
    def test_publish_routes(self):
        op, _ = self.opener()
        hdr = {"Cookie": f"sc_session={self.server_mod.make_token()}"}
        post = {**hdr, "Content-Type": "application/json", "X-ShortClip": "1"}
        body = lambda d: json.dumps(d).encode()  # noqa: E731
        big = json.dumps({"bible": {"title": "ก" * 30000}}).encode()  # ~90 KB of Thai
        self.assertEqual(self.call(op, "/api/plan/1/save-bible", big, post)[0], 404, "plan 1 absent, but body accepted")
        self.assertEqual(self.call(op, "/api/plan", big, post)[0], 500, "other routes keep the small limit")
        status, b = self.call(op, "/api/publish/accounts", headers=hdr)
        self.assertEqual(status, 200, b)
        self.assertEqual(sorted(json.loads(b)), ["instagram", "tiktok", "youtube"])
        status, b = self.call(op, "/api/episode/1/posts", headers=hdr)
        self.assertEqual(status, 200, b)
        self.assertIn("defaults", json.loads(b))
        self.assertEqual(self.call(op, "/publish.js", headers=hdr)[0], 200)
        self.assertEqual(self.call(op, "/api/episode/1/post", body({"platform": "youtube", "options": {}}), post)[0], 400,
                         "posting needs an explicit confirm")
        self.assertEqual(self.call(op, "/api/episode/1/post", body({"platform": "myspace", "confirm": True}), post)[0], 400)
        status, b = self.call(op, "/api/episode/1/post", body({"platform": "tiktok", "confirm": True,
                                                                "options": {"mode": "inbox"}}), post)
        self.assertEqual(status, 200, b)
        self.assertEqual(self.call(op, "/api/episode/1/post", body({"platform": "youtube", "confirm": True}),
                                   {"Content-Type": "application/json", "X-ShortClip": "1"})[0], 401, "login needed")
        # forged / unsigned public media link
        self.assertEqual(self.call(self.opener()[0], "/pub/" + "a" * 20 + "." + "0" * 40 + "/video.mp4")[0], 403)
        # OAuth callback is reachable without the session cookie, but a bad state is refused
        status, b = self.call(self.opener()[0], "/oauth/tiktok/callback?state=forged&code=x")
        self.assertEqual(status, 400)
        self.assertNotIn("login", b.lower()[:200])
        status, b = self.call(self.opener()[0], "/oauth/instagram/callback?error=access_denied&error_description=no")
        self.assertEqual(status, 400)
        self.assertIn("no", b)


class BibleEditTests(PlanningTests):
    def _ready(self):
        self.count = 3
        pid = self.pl.create("x", {})
        self.pl.concepts(pid)
        self.pl.choose(pid, {"id": "c1"})
        self.pl.bible(pid, {"episodes": 2})
        return pid

    def test_edit_add_delete_reorder_and_strict_lore(self):
        pid = self._ready()
        b = self.pl.get(pid)["bible"]
        b["characters"][0]["name"] = "นากใหม่"
        b["characters"].append({"id": "ch2", "name": "โอม", "look": "เสื้อช็อป", "abilities": ["วิ่งเร็ว"], "limits": ["กลัวผี"]})
        b["episodes"].append({"number": 99, "title": "ตอนเพิ่ม", "hook": "h", "summary": "s", "characters": ["ch2"]})
        b["episodes"] = [b["episodes"][2], b["episodes"][0], b["episodes"][1]]  # moved to the front
        p = self.pl.save_bible(pid, b)
        nb = p["bible"]
        self.assertEqual([e["number"] for e in nb["episodes"]], [1, 2, 3], "renumbered consecutively")
        self.assertEqual(nb["episodes"][0]["title"], "ตอนเพิ่ม")
        self.assertEqual(nb["characters"][0]["name"], "นากใหม่")
        self.assertEqual(p["bible_opts"]["episodes"], 3)
        bad = json.loads(json.dumps(nb))
        bad["lore"].append({"id": "lr9", "name": "แต่งเอง", "fact": "f", "source_url": "https://made.up/x"})
        with self.assertRaisesRegex(ValueError, "เลือกจากผลค้นหา"):
            self.pl.save_bible(pid, bad)
        bad["lore"][-1]["source_url"] = self.URL
        self.assertEqual(len(self.pl.save_bible(pid, bad)["bible"]["lore"]), 2)
        nochar = json.loads(json.dumps(nb))
        nochar["characters"][0]["limits"] = []
        with self.assertRaisesRegex(ValueError, "limits"):
            self.pl.save_bible(pid, nochar)

    def test_stale_edit_is_refused_and_approved_series_follows_edits(self):
        pid = self._ready()
        p = self.pl.get(pid)
        stale = p["updated_at"]
        self.con.execute("UPDATE plans SET updated_at='2000-01-01 00:00:00' WHERE id=?", (pid,))
        with self.assertRaisesRegex(ValueError, "ถูกแก้จากที่อื่น"):
            self.pl.save_bible(pid, p["bible"], expect_updated=stale)
        self.pl.approve(pid)
        b = self.pl.get(pid)["bible"]
        b["visual_rules"] = ["กลางวันทั้งตอน"]
        b["characters"][0]["look"] = "ผมสั้นสีแดง"
        self.pl.save_bible(pid, b)
        row = self.con.execute("SELECT rules_json,bible_json FROM series WHERE slug='ghost-uni'").fetchone()
        self.assertEqual(json.loads(row["rules_json"])["rules"]["visual"], ["กลางวันทั้งตอน"])
        self.assertIn("ผมสั้นสีแดง", row["bible_json"])
        self.assertIn("ผมสั้นสีแดง", (self.root / "projects/ghost-uni/bible.json").read_text())

    def test_ai_regen_one_character_keeps_the_rest(self):
        pid = self._ready()
        before = self.pl.get(pid)
        orig = self.fake_claude

        def fake(prompt, *, cwd, log_path, **kw):
            prompt = prompt() if callable(prompt) else prompt
            if "งาน D" not in prompt:
                return orig(prompt, cwd=cwd, log_path=log_path, **kw)
            self.prompts.append(prompt)
            log_path.write_text("x")
            self.assertIn("ให้มีความลับ", prompt)
            (Path(cwd) / "regen.json").write_text(json.dumps({"character": {
                "id": "zz", "name": "นาก AI", "look": "ใหม่", "abilities": ["a"], "limits": ["b"]}}, ensure_ascii=False))
            return {"total_cost_usd": 0.2, "usage": {}}
        self.planning.claude_json = fake
        p = self.pl.regen(pid, "character:ch1", "ให้มีความลับ")
        self.assertEqual(p["bible"]["characters"][0]["id"], "ch1", "id kept so episodes still point at it")
        self.assertEqual(p["bible"]["characters"][0]["name"], "นาก AI")
        self.assertEqual(p["bible"]["episodes"], before["bible"]["episodes"])
        self.assertEqual(p["status"], before["status"])
        self.assertAlmostEqual(p["cost_usd"], before["cost_usd"] + 0.2)
        with self.assertRaises(ValueError):
            self.pl.regen(pid, "character:nope")

    def test_failed_regen_keeps_bible_and_status(self):
        pid = self._ready()
        before = self.pl.get(pid)

        def broken(prompt, *, cwd, log_path, **kw):
            log_path.write_text("x")
            (Path(cwd) / "regen.json").write_text(json.dumps({"episode": {"title": ""}}))
            return {"total_cost_usd": 0.1, "usage": {}}
        self.planning.claude_json = broken
        with self.assertRaises(ValueError):
            self.pl.regen(pid, "episode:1")
        after = self.pl.get(pid)
        self.assertEqual(after["bible"], before["bible"])
        self.assertEqual(after["status"], "bible_ready")
        self.assertIn("ไม่สำเร็จ", after["error"])
        self.assertIsNone(after["busy_step"])
