"""Phase-1 job state machine. LLMs are subprocesses, never imported here."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import db
from .briefs import animator_brief, polish_prompt, writer_prompt
from .planning import bible_for_animator, bible_for_writer
from .compat import try_lock, unlock
from .external import StepError, claude_json, hyperframes, lint_zero, load_models, make_web, model_choices, newest_draft, parse_claude_log, probe_duration, tool
from .usage import merge_usage
from .validate import BEATS as BEATS_ALL, ValidationError, read_json, validate_script, validate_shots, verify_composition, write_manifest

ROLES = ("writer", "sound", "director", "animator", "editor", "qa")
VOICE_ENGINES = {"edge": ("th-TH-PremwadeeNeural", "th-TH-NiwatNeural"),
                 "wayu": ("f_young_warm", "f_young_clear", "f_young_bright", "f_teen_bright", "f_mid_clear",
                          "f_mid_warm", "f_elderly_soft", "f_elderly_low", "m_young_clear", "m_teen_bright",
                          "m_mid_warm", "m_elderly_deep")}
RULE_KEYS = ("story", "visual")


def clean_voice(cfg) -> dict | None:
    if not cfg:
        return None
    engine = str(cfg.get("engine") or "")
    voice = str(cfg.get("voice") or "")
    if engine not in VOICE_ENGINES or voice not in VOICE_ENGINES[engine]:
        raise StepError(f"unknown voice {engine}/{voice}")
    speed = float(cfg.get("speed") or 1.0)
    if not 0.6 <= speed <= 2.0:
        raise StepError("voice speed must be 0.6..2.0")
    return {"engine": engine, "voice": voice, "speed": round(speed, 3)}


def clean_rules(rules) -> dict:
    out = {}
    for key in RULE_KEYS:
        items = (rules or {}).get(key) or []
        if isinstance(items, str):
            items = items.splitlines()
        out[key] = [str(x).strip()[:300] for x in items if str(x).strip()][:30]
    return out
POSITION = {role: i for i, role in enumerate(ROLES, 1)}


class GateReached(RuntimeError):
    def __init__(self, gate):
        self.gate = gate
        super().__init__(gate)


class Pipeline:
    def __init__(self, root: Path, db_path: Path | None = None, handlers=None):
        self.root = Path(root)
        self.db_path = Path(db_path or os.environ.get("SHORTCLIP_DB", self.root / "db/shortclip.db"))
        self.con = db.connect(self.db_path)
        db.init_db(self.con)
        self.models = load_models(self.root)
        self.handlers = handlers or {}

    def close(self):
        self.con.close()

    def _job(self, job_id):
        row = self.con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise StepError(f"job {job_id} not found")
        return row

    def _episode(self, job):
        return self.con.execute("SELECT * FROM episodes WHERE id=?", (job["episode_id"],)).fetchone()

    def _steps(self, job_id):
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM steps WHERE job_id=? ORDER BY position,id", (job_id,))]

    @contextmanager
    def _lock(self, job_id):
        lockdir = self.db_path.parent / ".locks"
        lockdir.mkdir(parents=True, exist_ok=True)
        handle = (lockdir / f"job-{job_id}.lock").open("w")
        if not try_lock(handle):
            handle.close()
            raise StepError(f"job {job_id} is already being advanced by another process")
        try:
            yield
        finally:
            unlock(handle)
            handle.close()

    def series_settings(self, slug: str) -> dict:
        row = self.con.execute("SELECT rules_json,title,genre FROM series WHERE slug=?", (slug,)).fetchone()
        data = json.loads(row["rules_json"]) if row and row["rules_json"] else {}
        return {"slug": slug, "rules": clean_rules(data.get("rules")), "voice": data.get("voice"),
                "title": row["title"] if row else None, "genre": row["genre"] if row else None}

    def set_series(self, slug: str, *, rules=None, voice=None, title=None, genre=None) -> dict:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
            raise StepError("series slug must match [a-z0-9-]+")
        row = self.con.execute("SELECT id,rules_json,title,genre FROM series WHERE slug=?", (slug,)).fetchone()
        if not row:
            self.con.execute("INSERT INTO series(slug,title,genre) VALUES(?,?,?)",
                             (slug, title or slug, genre or "Thai 2D story short"))
            row = self.con.execute("SELECT id,rules_json,title,genre FROM series WHERE slug=?", (slug,)).fetchone()
        data = json.loads(row["rules_json"]) if row["rules_json"] else {}
        if rules is not None:
            data["rules"] = clean_rules(rules)
        if voice is not None:
            data["voice"] = clean_voice(voice)
        if title:
            self.con.execute("UPDATE series SET title=? WHERE id=?", (str(title)[:120], row["id"]))
        if genre:
            self.con.execute("UPDATE series SET genre=? WHERE id=?", (str(genre)[:120], row["id"]))
        self.con.execute("UPDATE series SET rules_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), row["id"]))
        return self.series_settings(slug)

    def set_voice(self, job_id: int, voice) -> dict:
        """Choose the narrator for an episode. Past gate1 this re-voices it: sound, timing, retime, karaoke,
        render, QA run again. The composition is reused (it reads SH/DUR), so no new Claude pass is paid
        unless the machine checks fail and the animator's one repair pass is needed."""
        cfg = clean_voice(voice)
        with self._lock(job_id):
            job = self._job(job_id)
            if job["status"] in {"running", "cancelled"}:
                raise StepError(f"job is {job['status']}; cannot change the voice now")
            epdir = Path(json.loads(job["params_json"] or "{}")["epdir"])
            (epdir / "voice.json").write_text(json.dumps(cfg, ensure_ascii=False))
            sound = next(s for s in self._steps(job_id) if s["role"] == "sound")
            db.event(self.con, job_id, f"voice set to {cfg['engine']}/{cfg['voice']} x{cfg['speed']}")
            if sound["status"] == "queued":
                return self.status(job_id)  # still before gate1/sound: nothing to redo
            self.con.execute("""UPDATE steps SET status='queued',pid=NULL,error=NULL
                                WHERE job_id=? AND position>=?""", (job_id, POSITION["sound"]))
            self.con.execute("UPDATE jobs SET status='queued',gate=NULL,error=NULL,finished_at=NULL WHERE id=?",
                             (job_id,))
            self.con.execute("UPDATE episodes SET status='script_ok',updated_at=datetime('now') WHERE id=?",
                             (job["episode_id"],))
        return self.run(job_id)

    def create(self, series: str, number: int, *, variant="", hook="countdown", idea="",
               fixture: str | None = None, requested_by="cli", run=True, voice=None, rules=None,
               target_seconds=None, models=None) -> dict:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", series):
            raise StepError("series slug must match [a-z0-9-]+")
        if number < 1:
            raise StepError("episode number must be positive")
        slug = series
        series_row = self.con.execute("SELECT id FROM series WHERE slug=?", (slug,)).fetchone()
        if series_row:
            series_id = series_row["id"]
        else:
            cur = self.con.execute("INSERT INTO series(slug,title,genre) VALUES(?,?,?)",
                                   (slug, slug, "Thai 2D story short"))
            series_id = cur.lastrowid
        epdir = self.root / "projects" / series / f"ep{number}{('v' + variant) if variant else ''}"
        if epdir.exists() and (epdir / "renders/final.mp4").exists():
            raise StepError(f"{epdir} already has final.mp4; choose another variant")
        epdir.mkdir(parents=True, exist_ok=True)
        (epdir / "renders").mkdir(exist_ok=True)
        (epdir / "backup").mkdir(exist_ok=True)
        settings = self.series_settings(slug)
        cfg = clean_voice(voice) or clean_voice(settings.get("voice"))
        if cfg:
            (epdir / "voice.json").write_text(json.dumps(cfg, ensure_ascii=False))
        target = int(target_seconds) if target_seconds else None
        if target is not None and not 50 <= target <= 70:
            raise StepError("target_seconds must be 50..70")
        params = {"hook": hook, "idea": idea, "fixture": fixture, "epdir": str(epdir),
                  "rules": clean_rules(rules), "target_seconds": target}
        if models:
            bad = {r: m for r, m in models.items() if r not in ("writer", "animator") or m not in model_choices(self.models)}
            if bad:
                raise StepError(f"unknown role/model: {bad}")
            params["models"] = dict(models)
        try:
            cur = self.con.execute(
                """INSERT INTO episodes(series_id,number,variant,title,target_seconds,hook_type,status)
                   VALUES(?,?,?,?,60,?,'draft')""", (series_id, number, variant, f"EP.{number}", hook))
            episode_id = cur.lastrowid
        except Exception as exc:
            raise StepError(f"episode {series} EP.{number} variant={variant!r} already exists") from exc
        job_id = self.con.execute(
            """INSERT INTO jobs(episode_id,kind,status,requested_by,params_json,run_id)
               VALUES(?,'new_episode','queued',?,?,?)""",
            (episode_id, requested_by, json.dumps(params, ensure_ascii=False), uuid.uuid4().hex)).lastrowid
        for role in ROLES:
            self.con.execute("INSERT INTO steps(job_id,role,status,attempt,position) VALUES(?,?,'queued',0,?)",
                             (job_id, role, POSITION[role]))
        db.event(self.con, job_id, f"created {series} EP.{number}", payload={"epdir": str(epdir)})
        if run:
            self.run(job_id)
        return self.status(job_id)

    def status(self, job_id=None):
        if job_id is not None:
            job = self._job(job_id)
            episode = self._episode(job)
            return {"job": dict(job), "episode": dict(episode), "steps": self._steps(job_id),
                    "events": [dict(r) for r in self.con.execute(
                        "SELECT * FROM events WHERE job_id=? ORDER BY id DESC LIMIT 20", (job_id,))]}
        return {"episodes": [dict(r) for r in self.con.execute("SELECT * FROM v_episode_cost ORDER BY episode_id")],
                "queue": [dict(r) for r in self.con.execute("SELECT * FROM v_queue")],
                "total_cost_usd": round(self.con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM steps").fetchone()[0], 4)}

    def list_jobs(self, limit=20):
        return [dict(r) for r in self.con.execute(
            """SELECT j.id,j.kind,j.status,j.gate,j.current_step,j.error,j.created_at,
                      e.number,e.variant,e.title,s.slug AS series
               FROM jobs j JOIN episodes e ON e.id=j.episode_id JOIN series s ON s.id=e.series_id
               ORDER BY j.id DESC LIMIT ?""", (limit,))]

    def run(self, job_id):
        with self._lock(job_id):
            job = self._job(job_id)
            if job["status"] == "done":
                return self.status(job_id)
            if job["status"] == "waiting_gate":
                return self.status(job_id)
            if job["status"] == "cancelled":
                raise StepError("job is cancelled")
            db.close_stale_steps(self.con, job_id)
            self.con.execute("UPDATE jobs SET status='running',error=NULL,started_at=COALESCE(started_at,datetime('now')),finished_at=NULL WHERE id=?", (job_id,))
            try:
                for step in self._steps(job_id):
                    if step["status"] == "done":
                        continue
                    self._run_step(job, step)
                # All six roles done means final QA is ready for the human gate.
                self._wait_gate(job_id, "gate2_final")
            except GateReached:
                pass
            except (StepError, ValidationError, OSError, ValueError) as exc:
                self.con.execute("UPDATE jobs SET status='failed',error=?,current_step=NULL WHERE id=?",
                                 (str(exc)[-2000:], job_id))
                db.event(self.con, job_id, str(exc), level="error")
                raise
            return self.status(job_id)

    def advance(self, job_id, decision, reason=""):
        if decision not in {"approve", "reject"}:
            raise StepError("advance requires approve=True/False")
        with self._lock(job_id):
            job = self._job(job_id)
            if job["status"] != "waiting_gate":
                raise StepError(f"job is {job['status']}, not waiting_gate")
            gate = job["gate"]
            if decision == "approve" and gate == "gate2_final":
                self.con.execute("""UPDATE jobs SET status='done',gate=NULL,current_step=NULL,
                                    finished_at=datetime('now'),error=NULL WHERE id=?""", (job_id,))
                self.con.execute("UPDATE episodes SET status='published-ready',updated_at=datetime('now') WHERE id=?",
                                 (job["episode_id"],))
                db.event(self.con, job_id, "gate2 approved; ready to publish", level="gate")
                return self.status(job_id)
            # Approval of gate1, or a rejection requesting a rewrite/polish.
            if decision == "reject":
                role = "writer" if gate == "gate1_script" else "animator"
                reason = reason.strip() or "ผู้ใช้ไม่อนุมัติ ให้เขียนใหม่โดยแก้จุดที่ควรปรับปรุง"
                # pending=True forces one new paid pass; it is cleared when that pass returns.
                self.con.execute("UPDATE steps SET input_json=? WHERE job_id=? AND role=?",
                                 (json.dumps({"revision": reason, "pending": True}, ensure_ascii=False),
                                  job_id, role))
                reset_from = POSITION[role]
                self.con.execute("""UPDATE steps SET status='queued',pid=NULL,error=NULL
                                    WHERE job_id=? AND position>=?""", (job_id, reset_from))
                if role == "writer":
                    self.con.execute("UPDATE episodes SET script_json=NULL,shots_json=NULL,duration=NULL,status='draft' WHERE id=?",
                                     (job["episode_id"],))
                db.event(self.con, job_id, f"{gate} rejected; restarting at {role}", level="warn")
            else:
                db.event(self.con, job_id, f"{gate} approved", level="gate")
            self.con.execute("UPDATE jobs SET status='queued',gate=NULL,error=NULL WHERE id=?", (job_id,))
        return self.run(job_id)

    def cancel(self, job_id):
        with self._lock(job_id):
            job = self._job(job_id)
            if job["status"] in {"done", "cancelled"}:
                raise StepError(f"job is already {job['status']}")
            self.con.execute("""UPDATE jobs SET status='cancelled',gate=NULL,current_step=NULL,
                                finished_at=datetime('now') WHERE id=?""", (job_id,))
            db.event(self.con, job_id, "cancelled by user", level="warn")
        return self.status(job_id)

    # ---- thumbnails (cover) ----

    def episode_dir(self, episode_id) -> Path:
        row = self.con.execute("""SELECT e.number,e.variant,s.slug,(SELECT params_json FROM jobs WHERE episode_id=e.id
                                  ORDER BY id DESC LIMIT 1) AS params FROM episodes e JOIN series s ON s.id=e.series_id
                                  WHERE e.id=?""", (episode_id,)).fetchone()
        if not row:
            raise StepError("episode not found")
        p = json.loads(row["params"] or "{}").get("epdir")
        return Path(p) if p else self.root / "projects" / row["slug"] / f"ep{row['number']}{('v' + row['variant']) if row['variant'] else ''}"

    def thumb_ideas(self, episode_id, count=5, extra="", model=None) -> dict:
        """One small paid Claude call (looks at the frames). Cost is added to the episode via an extra
        'thumb' event + the job's editor step usage so the dashboard totals stay right."""
        from . import thumbnail as T
        epdir = self.episode_dir(episode_id)
        model = model if model in model_choices(self.models) else self._model({}, "thumbnail")
        flag = T.tdir(epdir) / "ideas.running"
        flag.write_text(str(os.getpid()))
        try:
            n = len(list((epdir / "logs").glob("thumb-ideas-*.log"))) + 1
            log = epdir / "logs" / f"thumb-ideas-{n}.log"
            out = T.ideas(epdir, model, log, count=count, extra=extra)
        finally:
            flag.unlink(missing_ok=True)
        job = self.con.execute("SELECT id FROM jobs WHERE episode_id=? ORDER BY id DESC LIMIT 1", (episode_id,)).fetchone()
        if job:
            step = self.con.execute("SELECT id FROM steps WHERE job_id=? AND role='editor'", (job["id"],)).fetchone()
            if step:
                self._charge(step["id"], model, parse_claude_log(log), str(log))
            db.event(self.con, job["id"], f"thumbnail ideas x{len(out['ideas'])} (${out['cost_usd']:.2f})")
        return out

    # ---- edit any stage after the fact (also reopens a cancelled/failed/done job) ----

    REDO_FROM = ("writer", "sound", "animator")

    def _reopen(self, job, from_role):
        """Queue every step from `from_role` on; the job runs again from there."""
        self.con.execute("""UPDATE steps SET status='queued',pid=NULL,error=NULL
                            WHERE job_id=? AND position>=?""", (job["id"], POSITION[from_role]))
        self.con.execute("""UPDATE jobs SET status='queued',gate=NULL,error=NULL,finished_at=NULL,
                            current_step=NULL WHERE id=?""", (job["id"],))
        status = {"writer": "draft", "sound": "script_ok", "animator": "audio_ok"}[from_role]
        self.con.execute("UPDATE episodes SET status=?,updated_at=datetime('now') WHERE id=?",
                         (status, job["episode_id"]))

    def redo(self, job_id, from_role, revision="", *, fresh=False, beats=None, run=True):
        """Do a stage again. writer = AI rewrites the script (stops at gate1 again);
        sound = new narration (only `beats` if given; picture reused, $0 if checks pass);
        animator = fix the picture with `revision`, or draw it again from scratch (`fresh`)."""
        if from_role not in self.REDO_FROM:
            raise StepError(f"redo must start at one of {self.REDO_FROM}")
        revision = str(revision or "").strip()[:1500]
        with self._lock(job_id):
            job = self._job(job_id)
            epdir = Path(json.loads(job["params_json"] or "{}")["epdir"])
            steps = {s["role"]: s for s in self._steps(job_id)}
            if from_role == "writer":
                self.con.execute("UPDATE steps SET input_json=? WHERE job_id=? AND role='writer'",
                                 (json.dumps({"revision": revision or "เขียนบทตอนนี้ใหม่ทั้งหมด", "pending": True},
                                             ensure_ascii=False), job_id))
                self.con.execute("UPDATE episodes SET script_json=NULL,shots_json=NULL,duration=NULL WHERE id=?",
                                 (job["episode_id"],))
            elif from_role == "sound":
                if steps["writer"]["status"] != "done":
                    raise StepError("ยังไม่มีบทที่ผ่านแล้ว ทำเสียงไม่ได้")
                beatdir = epdir / "audio/beats"
                names = [b for b in (beats or []) if b in BEATS_ALL]
                if beats and not names:
                    raise StepError("unknown beat names")
                # beats=None: all new; beats=[]: keep every take, only re-time/re-mix (free)
                for b in (names if beats is not None else BEATS_ALL):
                    wav = beatdir / f"{b}.wav"
                    if wav.exists():
                        bk = epdir / "backup" / "beats-redo"
                        bk.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(wav), bk / f"{b}.wav")
                if beats is None or "hook" in names:
                    (beatdir / "_hook_boost.json").unlink(missing_ok=True)
            else:  # animator
                if steps["sound"]["status"] != "done" or not (epdir / "shots.json").exists():
                    raise StepError("ยังไม่มีเสียง/เวลา ทำภาพไม่ได้")
                index = epdir / "composition/index.html"
                if fresh or not index.is_file():
                    if index.is_file():
                        (epdir / "backup").mkdir(exist_ok=True)
                        shutil.move(str(index), epdir / "backup" / f"index.redraw-{steps['animator']['attempt']}.html")
                    self.con.execute("UPDATE steps SET input_json=NULL WHERE id=?", (steps["animator"]["id"],))
                elif revision:
                    self.con.execute("UPDATE steps SET input_json=? WHERE id=?",
                                     (json.dumps({"revision": revision, "pending": True}, ensure_ascii=False),
                                      steps["animator"]["id"]))
                else:
                    raise StepError("บอกว่าอยากให้แก้ภาพตรงไหน หรือเลือกวาดใหม่ทั้งหมด")
            self._reopen(job, from_role)
            db.event(self.con, job_id, f"redo from {from_role}" + (f": {revision[:200]}" if revision else "")
                     + (" (fresh)" if fresh else "") + (f" beats={beats}" if beats else ""), level="warn")
        return self.run(job_id) if run else self.status(job_id)

    def edit_script(self, job_id, data, *, run=True):
        """The user edits the script by hand. Before the voice exists it only saves (gate1 stays);
        afterwards voice, timing and subtitles are redone. Same line ids = the picture is reused and
        only retimed/re-subtitled ($0); added/removed/moved lines need a new drawing (paid)."""
        with self._lock(job_id):
            job = self._job(job_id)
            episode = self._episode(job)
            epdir = Path(json.loads(job["params_json"] or "{}")["epdir"])
            series = self.con.execute("SELECT slug FROM series WHERE id=?", (episode["series_id"],)).fetchone()[0]
            data = dict(data)
            data.setdefault("series", series)
            data.setdefault("episode", episode["number"])
            path = epdir / "script.json"
            old = read_json(path) if path.exists() else {}
            for key in ("logline", "title_reason", "target_seconds", "sfx_cues"):
                if key in old and key not in data:
                    data[key] = old[key]
            data = validate_script(data, series, episode["number"])
            steps = {s["role"]: s for s in self._steps(job_id)}
            if steps["writer"]["status"] != "done":
                raise StepError("นักเขียนบทยังเขียนไม่เสร็จ")
            sig = lambda d: [(x["id"], x["beat"]) for x in d.get("lines", [])]
            same_layout = sig(old) == sig(data)
            changed_beats = sorted({x["beat"] for x in data["lines"]} if not same_layout else
                                   {n["beat"] for n, o in zip(data["lines"], old["lines"]) if n["text"] != o["text"]})
            (epdir / "backup").mkdir(exist_ok=True)
            if path.exists():
                shutil.copy2(path, epdir / "backup" / f"script.before-edit-{int(__import__('time').time())}.json")
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            self.con.execute("UPDATE episodes SET title=?,hook_type=?,script_json=?,updated_at=datetime('now') WHERE id=?",
                             (data["title"], data["hook_type"], json.dumps(data, ensure_ascii=False), episode["id"]))
            db.event(self.con, job_id, f"script edited by user; beats changed: {changed_beats or 'none'}",
                     level="warn", payload={"same_layout": same_layout})
            result = {"saved": True, "same_layout": same_layout, "changed_beats": changed_beats, "rerun": None}
            if steps["sound"]["status"] == "queued" and steps["sound"]["attempt"] == 0:
                # Never voiced yet: just (re)open gate1 so the user approves the edited script.
                self.con.execute("""UPDATE jobs SET status='waiting_gate',gate='gate1_script',error=NULL,
                                    finished_at=NULL,current_step=NULL WHERE id=?""", (job_id,))
                self.con.execute("UPDATE episodes SET status='script_ok' WHERE id=?", (episode["id"],))
                return {**result, **self.status(job_id)}
            beatdir = epdir / "audio/beats"
            for b in changed_beats:
                if (beatdir / f"{b}.wav").exists():
                    (epdir / "backup/beats-redo").mkdir(parents=True, exist_ok=True)
                    shutil.move(str(beatdir / f"{b}.wav"), epdir / "backup/beats-redo" / f"{b}.wav")
            if "hook" in changed_beats:
                (beatdir / "_hook_boost.json").unlink(missing_ok=True)
            index = epdir / "composition/index.html"
            if not same_layout and index.is_file():
                shutil.move(str(index), epdir / "backup" / f"index.before-script-edit-{steps['animator']['attempt']}.html")
            self._reopen(job, "sound")
            result["rerun"] = "sound" if same_layout else "sound+animator"
        out = self.run(job_id) if run else self.status(job_id)
        return {**result, **out}

    def resume(self, job_id):
        job = self._job(job_id)
        if job["status"] == "failed":
            step = next((s for s in self._steps(job_id) if s["status"] == "failed"), None)
            if step:
                self.con.execute("UPDATE steps SET status='queued',error=NULL,pid=NULL WHERE id=?", (step["id"],))
            self.con.execute("UPDATE jobs SET status='queued',error=NULL WHERE id=?", (job_id,))
        elif job["status"] == "running":
            self.con.execute("UPDATE jobs SET status='queued' WHERE id=?", (job_id,))
        return self.run(job_id)

    def _wait_gate(self, job_id, gate):
        status = "script_ok" if gate == "gate1_script" else "qa_ok"
        job = self._job(job_id)
        self.con.execute("UPDATE jobs SET status='waiting_gate',gate=?,current_step=NULL WHERE id=?", (gate, job_id))
        self.con.execute("UPDATE episodes SET status=?,updated_at=datetime('now') WHERE id=?",
                         (status, job["episode_id"]))
        db.event(self.con, job_id, f"waiting for human approval: {gate}", level="gate")
        raise GateReached(gate)

    def _run_step(self, job, step):
        job_id, role = job["id"], step["role"]
        episode = self._episode(job)
        params = json.loads(job["params_json"] or "{}")
        epdir = Path(params["epdir"])
        log = epdir / "logs" / f"{POSITION[role]:02d}-{role}-attempt{step['attempt'] + 1}.log"
        self.con.execute("""UPDATE steps SET status='running',attempt=attempt+1,pid=?,log_path=?,
                            started_at=datetime('now'),finished_at=NULL,error=NULL WHERE id=?""",
                         (os.getpid(), str(log), step["id"]))
        self.con.execute("UPDATE jobs SET current_step=? WHERE id=?", (role, job_id))
        db.event(self.con, job_id, f"start {role} (attempt {step['attempt'] + 1})")
        handler = self.handlers.get(role) or getattr(self, f"_{role}")
        try:
            output = handler(job, episode, step, epdir, log) or {}
            output = {k: v for k, v in output.items() if k not in {"usage_json", "cost_usd", "model"}}
            self.con.execute("""UPDATE steps SET status='done',pid=NULL,output_json=?,finished_at=datetime('now')
                                WHERE id=?""", (json.dumps(output, ensure_ascii=False, default=str), step["id"]))
            db.event(self.con, job_id, f"done {role}")
            if role == "writer":
                self._wait_gate(job_id, "gate1_script")
        except GateReached:
            raise
        except Exception as exc:
            self.con.execute("UPDATE steps SET status='failed',pid=NULL,error=?,finished_at=datetime('now') WHERE id=?",
                             (str(exc)[-2000:], step["id"]))
            raise

    # ---- paid passes: every Claude result is charged exactly once, keyed by its log file ----

    def _charge(self, step_id, model, result, log):
        row = self.con.execute("SELECT usage_json,cost_usd FROM steps WHERE id=?", (step_id,)).fetchone()
        usage = json.loads(row["usage_json"]) if row["usage_json"] else {}
        counted = list(usage.get("counted", []))
        if str(log) in counted:
            return
        merged = merge_usage([usage, result.get("usage", {})])
        merged["passes"] = int(usage.get("passes") or 0) + 1
        merged["counted"] = counted + [str(log)]
        self.con.execute("UPDATE steps SET model=?,usage_json=?,cost_usd=? WHERE id=?",
                         (model, json.dumps(merged, ensure_ascii=False),
                          round(float(row["cost_usd"] or 0) + float(result.get("total_cost_usd") or 0), 6),
                          step_id))

    @staticmethod
    def _input(step):
        try:
            return json.loads(step["input_json"] or "{}")
        except ValueError:
            return {}

    def _revision_done(self, step_id, revision):
        self.con.execute("UPDATE steps SET input_json=? WHERE id=?",
                         (json.dumps({"revision": revision, "pending": False}, ensure_ascii=False), step_id))

    def _paid_before(self, step, model):
        """True when an earlier attempt's Claude pass finished: reuse its output, do not pay again.

        Look at every earlier main log of this role, not only the previous attempt: a re-voice
        attempt that only ran retime/render writes no Claude log, and the next re-voice must
        still find the finished pass two attempts back.
        """
        if not step["attempt"]:
            return False
        candidates = []
        if step.get("log_path"):
            candidates.append(Path(step["log_path"]))
            logdir = Path(step["log_path"]).parent
            pat = re.compile(rf"{POSITION[step['role']]:02d}-{step['role']}-attempt(\d+)\.log$")
            found = sorted((int(m.group(1)), p) for p in logdir.glob(f"*-{step['role']}-attempt*.log")
                           if (m := pat.search(p.name)))
            candidates += [p for _, p in reversed(found)]
        for path in candidates:
            try:
                result = parse_claude_log(path)
            except (StepError, OSError):
                continue
            if result.get("subtype") == "success" and not result.get("is_error"):
                self._charge(step["id"], model, result, str(path))  # no-op if already counted
                return True
        return False

    def _writer(self, job, episode, step, epdir, log):
        params = json.loads(job["params_json"] or "{}")
        series = self.con.execute("SELECT slug FROM series WHERE id=?", (episode["series_id"],)).fetchone()[0]
        number = episode["number"]
        script_path = epdir / "script.json"
        model = self._model(params, "writer")
        inp = self._input(step)
        revision, pending = inp.get("revision", ""), bool(inp.get("pending"))
        data = None
        if params.get("fixture"):
            data = read_json(Path(params["fixture"]))
        elif not pending and script_path.exists() and self._paid_before(step, model):
            try:
                data = validate_script(read_json(script_path), series, number)
            except ValidationError:
                data = None
        if data is None:
            idea = params.get("idea", "")
            if revision:
                idea += f"\nผู้ใช้ไม่อนุมัติร่างก่อนหน้า ให้เขียนใหม่โดยแก้ตามนี้: {revision}"
            if pending and script_path.exists():
                # The rejected draft must not be able to pass as the rewrite.
                rejected = epdir / "backup" / f"script.rejected-{step['attempt']}.json"
                rejected.parent.mkdir(exist_ok=True)
                shutil.move(str(script_path), rejected)
                idea += f"\nร่างที่ถูกปฏิเสธอยู่ที่ {rejected} (อ่านเพื่อรู้ว่าต้องเปลี่ยนอะไร ห้ามคัดลอก)"
            prompt = writer_prompt(self.root, epdir, series, number, params.get("hook", "countdown"), idea.strip(),
                                   rules=self._rules(series, params, "story"),
                                   target_seconds=params.get("target_seconds")) + \
                bible_for_writer(self._bible(series), number)
            errors = []
            for call_log in (log, log.with_name(log.stem + "-retry.log")):
                result = claude_json(prompt, model=model, cwd=epdir, log_path=call_log,
                                     max_turns=45, timeout=1800, tools="Read,Write,Edit,Bash,Glob,Grep")
                self._charge(step["id"], model, result, call_log)
                if pending:
                    self._revision_done(step["id"], revision)
                    pending = False
                try:
                    data = validate_script(read_json(script_path), series, number)
                    break
                except ValidationError as exc:
                    errors.append(str(exc))
                    prompt = (f"แก้ {script_path} ให้ผ่าน schema เดิม ข้อผิดพลาด: {exc}\n"
                              f"อ่าน {self.root / 'agents/writer.md'} แล้วเขียนไฟล์เดิมให้ valid เท่านั้น")
            if data is None:
                raise StepError("writer output invalid after retry: " + " | ".join(errors))
        validate_script(data, series, number)
        script_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        self.con.execute("""UPDATE episodes SET title=?,hook_type=?,script_json=?,status='script_ok',updated_at=datetime('now')
                            WHERE id=?""", (data["title"], data["hook_type"],
                                            json.dumps(data, ensure_ascii=False), episode["id"]))
        db.artifact(self.con, episode["id"], step["id"], "script", epdir / "script.json")
        return {"title": data["title"], "title_options": data["title_options"],
                "hook": data["hook_type"], "lines": len(data["lines"]),
                "first_lines": [x["text"] for x in data["lines"][:3]]}

    def _sound(self, job, episode, step, epdir, log):
        result = tool("tts_natural.py", epdir, log, timeout=2700)
        mix = epdir / "audio/mix.wav"
        if not mix.is_file():
            raise StepError("tts_natural completed without audio/mix.wav")
        db.artifact(self.con, episode["id"], step["id"], "mix", mix)
        self.con.execute("UPDATE episodes SET status='audio_ok',updated_at=datetime('now') WHERE id=?", (episode["id"],))
        return {"tail": result["tail"][-1000:]}

    def _director(self, job, episode, step, epdir, log):
        script = read_json(epdir / "script.json")
        shots = validate_shots(read_json(epdir / "shots.json"), script)
        tool("make_srt.py", epdir, log, timeout=120)
        db.artifact(self.con, episode["id"], step["id"], "shots", epdir / "shots.json")
        db.artifact(self.con, episode["id"], step["id"], "srt", epdir / "renders" / f"{epdir.name}.srt")
        self.con.execute("UPDATE episodes SET shots_json=?,duration=?,updated_at=datetime('now') WHERE id=?",
                         (json.dumps(shots, ensure_ascii=False), shots["duration"], episode["id"]))
        return {"duration": shots["duration"], "shots": len(shots["shots"]), "align": shots.get("align")}

    def _model(self, params, role):
        """Per-episode choice (params.models[role]) → config role default; re-read config so dashboard edits apply."""
        self.models = load_models(self.root)
        chosen = (params.get("models") or {}).get(role)
        if chosen and chosen in model_choices(self.models):
            return chosen
        return self.models.get(role, self.models["default"])

    def _bible(self, series_slug):
        row = self.con.execute("SELECT bible_json FROM series WHERE slug=?", (series_slug,)).fetchone()
        return json.loads(row["bible_json"]) if row and row["bible_json"] else None

    def _rules(self, series_slug, params, key):
        return self.series_settings(series_slug)["rules"].get(key, []) + (params.get("rules") or {}).get(key, [])

    def _reference_episode(self, epdir):
        candidates = [p.parent.parent for p in (self.root / "projects" / epdir.parent.name).glob("ep*/composition/index.html")
                      if p.parent.parent != epdir]
        return sorted(candidates, key=lambda p: (p / "composition/index.html").stat().st_mtime)[-1] if candidates else None

    def _prepare_composition(self, epdir, shots, visual_rules=(), bible_text=""):
        comp = epdir / "composition"
        comp.mkdir(exist_ok=True)
        ref = self._reference_episode(epdir)
        if ref:
            refcomp = ref / "composition"
            if (refcomp / "assets").exists() and not (comp / "assets").exists():
                shutil.copytree(refcomp / "assets", comp / "assets")
            for name in ("hyperframes.json", "package.json"):
                if (refcomp / name).exists():
                    shutil.copy2(refcomp / name, comp / name)
        shutil.copy2(self.root / "CLAUDE.md", comp / "CLAUDE.md")
        (comp / "BRIEF.md").write_text(animator_brief(self.root, epdir, shots, ref, rules=visual_rules) + bible_text)
        return comp

    def _animator(self, job, episode, step, epdir, log):
        shots = read_json(epdir / "shots.json")
        series = self.con.execute("SELECT slug FROM series WHERE id=?", (episode["series_id"],)).fetchone()[0]
        comp = self._prepare_composition(epdir, shots,
                                         self._rules(series, json.loads(job["params_json"] or "{}"), "visual"),
                                         bible_for_animator(self._bible(series), episode["number"]))
        index = comp / "index.html"
        model = self._model(json.loads(job["params_json"] or "{}"), "animator")
        inp = self._input(step)
        revision, pending = inp.get("revision", ""), bool(inp.get("pending"))

        def paid(prompt, call_log, turns):
            result = claude_json(prompt, model=model, cwd=comp, log_path=call_log, max_turns=turns, timeout=3600)
            self._charge(step["id"], model, result, call_log)

        if pending:
            if not index.is_file():
                raise StepError("cannot polish: composition/index.html is missing")
            (epdir / "backup").mkdir(exist_ok=True)
            shutil.copy2(index, epdir / "backup" / f"index.before-polish-{step['attempt']}.html")
            paid(polish_prompt(epdir, revision), log, 70)
            self._revision_done(step["id"], revision)
        elif not (index.is_file() and self._paid_before(step, model)):
            paid("อ่าน BRIEF.md แล้วทำทุกข้อให้ครบ ห้ามแตะ ../audio/", log, 90)
            if not index.is_file():
                paid("ยังไม่มี index.html ให้อ่าน BRIEF.md และเขียนไฟล์ให้เสร็จแล้ว lint/render ตาม brief",
                     log.with_name(log.stem + "-retry.log"), 90)
        try:
            return self._finish_composition(episode, step, epdir, comp, shots, log)
        except StepError as exc:
            # One bounded repair pass per run: the machine checks failed, tell Claude exactly why.
            paid(f"index.html ไม่ผ่านการตรวจของระบบ ให้แก้เฉพาะจุดนี้แล้ว lint ให้ได้ 0 error:\n{str(exc)[-1500:]}\n"
                 "อ่าน BRIEF.md หัวข้อ 'สัญญากับระบบ' ห้ามเปลี่ยนข้อความซับและห้ามเปลี่ยน rig/palette",
                 log.with_name(log.stem + "-repair.log"), 40)
            return self._finish_composition(episode, step, epdir, comp, shots,
                                            log.with_name(log.stem + "-afterrepair.log"))

    def _finish_composition(self, episode, step, epdir, comp, shots, log):
        """Machine checks only (no LLM): timing contract, lint, render, duration."""
        index = comp / "index.html"
        if not index.is_file():
            raise StepError("animator returned without composition/index.html")
        if verify_composition(index.read_text(), shots):
            # Timing drift is repaired deterministically; retime exits 1 if anything is still wrong.
            tool("retime.py", epdir, log.with_name(log.stem + "-retime.log"), timeout=120)
        if any(s.get("words") for s in shots["shots"]):
            # Word-by-word karaoke highlight, written by code from shots.json (never by the LLM).
            tool("karaoke.py", epdir, log.with_name(log.stem + "-karaoke.log"), timeout=120)
            problems = verify_composition(index.read_text(), shots)
            if problems:
                raise StepError("karaoke broke the composition contract: " + "; ".join(problems[:5]))
        lint = hyperframes(comp, "lint", log.with_name(log.stem + "-lint.log"))
        if not lint_zero(lint["tail"]):
            raise StepError("hyperframes lint is not 0 errors: " + lint["tail"][-600:])
        draft = comp / "renders/draft.mp4"
        if not draft.exists() or draft.stat().st_mtime < index.stat().st_mtime:
            hyperframes(comp, "render", log.with_name(log.stem + "-render.log"))
            newest = newest_draft(comp)
            if not newest:
                raise StepError("no rendered MP4")
            if newest != draft:
                shutil.move(str(newest), draft)
        actual = probe_duration(draft, comp, log.with_name(log.stem + "-probe.log"))
        if abs(actual - shots["duration"]) > 0.2:
            raise StepError(f"draft duration {actual:.3f}s != timeline {shots['duration']:.3f}s")
        db.artifact(self.con, episode["id"], step["id"], "draft", draft)
        self.con.execute("UPDATE episodes SET status='video_ok',updated_at=datetime('now') WHERE id=?", (episode["id"],))
        return {"draft": str(draft), "duration": actual}

    def _editor(self, job, episode, step, epdir, log):
        tool("finalize.py", epdir, log, timeout=900)
        qa = read_json(epdir / "qa.json")
        if not qa.get("passed"):
            raise StepError("QA failed: " + "; ".join(qa.get("fails", [])))
        web = make_web(epdir, log.with_name(log.stem + "-web.log"))
        db.artifact(self.con, episode["id"], step["id"], "final", epdir / "renders/final.mp4")
        db.artifact(self.con, episode["id"], step["id"], "web", web)
        db.artifact(self.con, episode["id"], step["id"], "qa", epdir / "qa.json")
        return {"duration": qa["video"]["duration"], "lufs": qa.get("audio", {}).get("lufs"),
                "web_bytes": web.stat().st_size}

    def _qa(self, job, episode, step, epdir, log):
        script = read_json(epdir / "script.json")
        shots = validate_shots(read_json(epdir / "shots.json"), script)
        qa = read_json(epdir / "qa.json")
        if not qa.get("passed"):
            raise StepError("QA gate failed")
        web = epdir / "renders/web.mp4"
        if not web.exists() or web.stat().st_size >= 10_000_000:
            raise StepError("web.mp4 missing or >=10 MB")
        steps = self._steps(job["id"])
        for s in steps:
            s["usage_json"] = json.loads(s["usage_json"]) if s.get("usage_json") else {}
        manifest = write_manifest(epdir, script, shots, qa, steps)
        db.artifact(self.con, episode["id"], step["id"], "manifest", manifest)
        self.con.execute("UPDATE episodes SET status='qa_ok',duration=?,updated_at=datetime('now') WHERE id=?",
                         (qa["video"]["duration"], episode["id"]))
        db.event(self.con, job["id"], "QA passed; final ready for gate2", level="gate",
                 payload={"duration": qa["video"]["duration"], "lufs": qa.get("audio", {}).get("lufs")})
        return {"passed": True, "manifest": str(manifest)}
