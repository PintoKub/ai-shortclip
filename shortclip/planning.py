"""Story planning before production: idea → N concepts → choose (+edit) → summary → series bible → series.

Each Claude call writes one JSON file in `plans/<id>/`, is validated by code, and is charged once
(keyed by its log file, like the pipeline). The dashboard starts these as detached processes
(`orchestrator.py plan ...`) and polls `plans.status`.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import db
from .external import StepError, claude_json, load_models, model_choices, parse_claude_log
from .usage import merge_usage

HOOKS = {"countdown", "forbidden", "midscene", "lie"}
LORE_KINDS = {"place", "legend", "history", "person", "culture"}
OPTION_KEYS = ("genre", "tone", "audience", "location", "must", "avoid")


class PlanError(ValueError):
    pass


def need(cond, msg):
    if not cond:
        raise PlanError(msg)


def _s(x, n=400):
    return str(x or "").strip()[:n]


def slugify(text: str, fallback="series") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40]
    return s or fallback


# ---------- validators (an LLM cannot mark its own work valid) ----------

def validate_concepts(data, count: int) -> list[dict]:
    need(isinstance(data, dict) and isinstance(data.get("concepts"), list), "concepts: need {'concepts': [...]}")
    items = data["concepts"]
    need(len(items) == count, f"concepts: need exactly {count}, got {len(items)}")
    out, titles = [], set()
    for i, c in enumerate(items, 1):
        need(isinstance(c, dict), f"concept {i} must be an object")
        for key in ("title", "logline", "hook_3s", "why_watch", "season_arc"):
            need(_s(c.get(key)), f"concept {i}: {key} missing")
        need(_s(c["title"]) not in titles, f"concept {i}: duplicate title")
        titles.add(_s(c["title"]))
        seeds = [_s(x, 300) for x in (c.get("episode_seeds") or []) if _s(x)]
        need(len(seeds) >= 2, f"concept {i}: need ≥2 episode_seeds")
        out.append({"id": f"c{i}", "title": _s(c["title"], 80), "slug": slugify(c.get("slug") or "", f"series-{i}"),
                    "logline": _s(c["logline"]), "genre": _s(c.get("genre"), 80), "tone": _s(c.get("tone"), 80),
                    "audience": _s(c.get("audience"), 80), "hook_3s": _s(c["hook_3s"]),
                    "why_watch": _s(c["why_watch"]), "real_anchor": _s(c.get("real_anchor")),
                    "season_arc": _s(c["season_arc"], 800), "episode_seeds": seeds[:12], "risk": _s(c.get("risk"))})
    return out


def validate_summary(data) -> dict:
    need(isinstance(data, dict), "summary must be an object")
    for key in ("title", "logline", "premise", "season_arc", "core_mystery"):
        need(_s(data.get(key)), f"summary: {key} missing")
    out: dict = {k: _s(data.get(k), 1200) for k in ("title", "logline", "genre", "tone", "audience", "premise",
                                               "season_arc", "core_mystery", "real_anchor")}
    out["slug"] = slugify(data.get("slug") or "", "series")
    out["selling_points"] = [_s(x) for x in (data.get("selling_points") or []) if _s(x)][:6]
    out["open_questions"] = [_s(x) for x in (data.get("open_questions") or []) if _s(x)][:6]
    return out


def known_urls(cache: Path) -> set[str]:
    urls = set()
    if cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            try:
                urls.add(json.loads(line)["url"])
            except (ValueError, KeyError, TypeError):
                continue
    return urls


def validate_bible(data, episodes: int, start: int, cache: Path, strict: bool = False) -> tuple[dict, list[str]]:
    """Returns (clean bible, warnings). Lore whose URL was not fetched by lore_research is dropped
    (strict=True, for user edits: refused with a message instead, so nothing vanishes silently)."""
    need(isinstance(data, dict), "bible must be an object")
    for key in ("title", "logline"):
        need(_s(data.get(key)), f"bible: {key} missing")
    warnings = []
    chars = data.get("characters") or []
    need(1 <= len(chars) <= 8, "bible: need 1–8 characters")
    clean_chars, ids = [], set()
    for i, c in enumerate(chars, 1):
        need(_s(c.get("name")), f"character {i}: name missing")
        need(_s(c.get("look")), f"character {i}: look missing (the animator draws it)")
        abil = [_s(x, 200) for x in (c.get("abilities") or []) if _s(x)]
        lim = [_s(x, 200) for x in (c.get("limits") or []) if _s(x)]
        need(abil and lim, f"character {i} ({c.get('name')}): needs abilities and limits")
        cid = _s(c.get("id"), 10) or f"ch{i}"
        need(cid not in ids, f"character id {cid} duplicated")
        ids.add(cid)
        clean_chars.append({"id": cid, "name": _s(c["name"], 60), "role": _s(c.get("role"), 40),
                            "age": _s(c.get("age"), 30), "look": _s(c["look"]), "personality": _s(c.get("personality")),
                            "abilities": abil[:6], "limits": lim[:6], "goal": _s(c.get("goal")),
                            "secret": _s(c.get("secret")), "arc": _s(c.get("arc")),
                            "first_episode": int(c.get("first_episode") or start)})
    urls = known_urls(cache)
    lore, lore_ids = [], set()
    for i, item in enumerate(data.get("lore") or [], 1):
        url = _s(item.get("source_url"), 500)
        if url not in urls:
            need(not strict, f"lore '{_s(item.get('name'), 60)}': แหล่งอ้างอิงต้องเลือกจากผลค้นหา (ค้นแหล่งในหน้า lore)")
            warnings.append(f"ตัด lore '{_s(item.get('name'), 60)}' ออก: แหล่งอ้างอิงไม่ได้มาจากการค้นจริง")
            continue
        need(_s(item.get("name")) and _s(item.get("fact")), f"lore {i}: ต้องมีชื่อและข้อเท็จจริง")
        lid = _s(item.get("id"), 10) or f"lr{i}"
        lore_ids.add(lid)
        kind = _s(item.get("kind"), 20)
        lore.append({"id": lid, "kind": kind if kind in LORE_KINDS else "culture", "name": _s(item.get("name"), 80),
                     "fact": _s(item.get("fact"), 600), "source_url": url, "twist": _s(item.get("twist"), 600),
                     "used_in": [int(x) for x in (item.get("used_in") or []) if str(x).isdigit()]})
    if not lore:
        warnings.append("ไม่มี lore ที่ตรวจแหล่งได้ — เรื่องจะไม่อิงของจริง")
    eps = data.get("episodes") or []
    need(len(eps) == episodes, f"bible: need exactly {episodes} episodes, got {len(eps)}")
    clean_eps = []
    for i, e in enumerate(eps):
        n = start + i
        need(int(e.get("number") or 0) == n, f"episode {i + 1}: number must be {n}")
        for key in ("title", "summary", "hook"):
            need(_s(e.get(key)), f"episode {n}: {key} missing")
        hook_type = _s(e.get("hook_type"), 20)
        clean_eps.append({"number": n, "title": _s(e["title"], 80), "hook_type": hook_type if hook_type in HOOKS else "countdown",
                          "hook": _s(e["hook"]), "summary": _s(e["summary"], 900), "location": _s(e.get("location")),
                          "characters": [x for x in (e.get("characters") or []) if x in ids],
                          "lore": [x for x in (e.get("lore") or []) if x in lore_ids],
                          "twist": _s(e.get("twist")), "cliffhanger": _s(e.get("cliffhanger"))})
    world = data.get("world") or {}
    vs = data.get("visual_style") or {}
    bible = {"title": _s(data["title"], 80), "slug": slugify(data.get("slug") or "", "series"),
             "logline": _s(data["logline"]), "genre": _s(data.get("genre"), 80), "tone": _s(data.get("tone"), 80),
             "audience": _s(data.get("audience"), 80),
             "world": {"setting": _s(world.get("setting"), 600), "era": _s(world.get("era"), 80),
                       "rules": [_s(x, 200) for x in (world.get("rules") or []) if _s(x)][:10]},
             "characters": clean_chars, "lore": lore, "episodes": clean_eps,
             "visual_style": {"palette": _s(vs.get("palette"), 200),
                              "recurring_places": [_s(x, 100) for x in (vs.get("recurring_places") or []) if _s(x)][:8],
                              "notes": _s(vs.get("notes"), 600)},
             "story_rules": [_s(x, 300) for x in (data.get("story_rules") or []) if _s(x)][:15],
             "visual_rules": [_s(x, 300) for x in (data.get("visual_rules") or []) if _s(x)][:15]}
    return bible, warnings


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def existing_parts(d: Path) -> list[tuple[str, str]]:
    """Valid bible.part-N.json files already written, with a short note of what each holds."""
    out = []
    for p in sorted(d.glob("bible.part-*.json"), key=lambda p: int(re.search(r"part-(\d+)", p.name).group(1))):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            p.unlink(missing_ok=True)  # half-written part: let Claude redo it
            continue
        bits = []
        for k, v in data.items():
            if k == "episodes" and isinstance(v, list):
                bits.append("EP." + ",".join(str(e.get("number")) for e in v if isinstance(e, dict)))
            elif isinstance(v, list):
                bits.append(f"{k} {len(v)}")
        out.append((p.name, " ".join(bits) or "ข้อมูลหลัก"))
    return out


def bible_for_writer(bible: dict, number: int) -> str:
    """Compact text block the writer gets for one episode."""
    if not bible:
        return ""
    ep = next((e for e in bible.get("episodes", []) if e["number"] == number), None)
    chars = {c["id"]: c for c in bible.get("characters", [])}
    lore = {x["id"]: x for x in bible.get("lore", [])}
    lines = [f"\n## Series bible: {bible.get('title')} (ต้องสอดคล้อง)", f"- แก่นเรื่อง: {bible.get('logline')}"]
    w = bible.get("world") or {}
    if w.get("setting"):
        lines.append(f"- โลก: {w['setting']}")
    for r in w.get("rules", []):
        lines.append(f"- กฎของโลก: {r}")
    use = ep["characters"] if ep and ep.get("characters") else list(chars)
    for cid in use:
        c = chars.get(cid)
        if c:
            lines.append(f"- ตัวละคร {c['name']} ({c['role']}): {c['personality']} | ทำได้: {', '.join(c['abilities'])} "
                         f"| ทำไม่ได้: {', '.join(c['limits'])} | เป้าหมาย: {c['goal']}")
    if ep:
        lines.append(f"\n## แผนตอนนี้ (EP.{number}: {ep['title']}) ให้เขียนตามนี้")
        lines += [f"- hook 3 วิแรก: {ep['hook']}", f"- เรื่องย่อ: {ep['summary']}", f"- สถานที่: {ep['location']}",
                  f"- twist: {ep['twist']}", f"- cliffhanger: {ep['cliffhanger']}"]
        for lid in ep.get("lore", []):
            x = lore.get(lid)
            if x:
                lines.append(f"- อิงของจริง: {x['name']} — ข้อเท็จจริง: {x['fact']} | ที่เราแต่งต่อ: {x['twist']}")
    return "\n".join(lines) + "\n"


def bible_for_animator(bible: dict, number: int) -> str:
    if not bible:
        return ""
    ep = next((e for e in bible.get("episodes", []) if e["number"] == number), None)
    chars = [c for c in bible.get("characters", []) if not ep or not ep.get("characters") or c["id"] in ep["characters"]]
    vs = bible.get("visual_style") or {}
    lines = ["\n## ตัวละครและสไตล์จาก series bible (วาดให้ตรง)"]
    lines += [f"- {c['name']}: {c['look']}" for c in chars]
    if vs.get("palette"):
        lines.append(f"- โทนสี: {vs['palette']}")
    if ep and ep.get("location"):
        lines.append(f"- สถานที่ตอนนี้: {ep['location']}")
    return "\n".join(lines) + "\n"


# ---------- the planner (Claude calls) ----------

class Planner:
    def __init__(self, root: Path, con):
        self.root = Path(root)
        self.con = con
        self.models = load_models(self.root)
        self.model = self.models.get("planner", self.models["default"])

    def dir(self, pid: int) -> Path:
        base = Path(os.environ["SHORTCLIP_PLANS"]) if os.environ.get("SHORTCLIP_PLANS") else self.root / "plans"
        d = base / str(pid)  # SHORTCLIP_PLANS: selftests work on a copy, never on the user's plans
        (d / "logs").mkdir(parents=True, exist_ok=True)
        return d

    def get(self, pid: int) -> dict:
        row = self.con.execute("SELECT * FROM plans WHERE id=?", (pid,)).fetchone()
        need(row is not None, f"plan {pid} not found")
        out = dict(row)
        for k in ("options_json", "concepts_json", "chosen_json", "summary_json", "bible_opts_json",
                  "bible_json", "usage_json", "warnings_json"):
            out[k[:-5]] = json.loads(out.pop(k)) if out.get(k) else None
        return out

    def list(self, limit=30) -> list[dict]:
        return [{"id": r["id"], "status": r["status"], "idea": (r["idea"] or "")[:120], "busy_step": r["busy_step"],
                 "series_slug": r["series_slug"], "cost_usd": r["cost_usd"], "updated_at": r["updated_at"],
                 "title": (json.loads(r["summary_json"]).get("title") if r["summary_json"] else None)}
                for r in self.con.execute("SELECT * FROM plans ORDER BY id DESC LIMIT ?", (limit,))]

    def _set(self, plan_id, **kw):
        cols = ", ".join(f"{k}=?" for k in kw)
        self.con.execute(f"UPDATE plans SET {cols}, updated_at=datetime('now') WHERE id=?", (*kw.values(), plan_id))

    def create(self, idea: str, options: dict | None = None) -> int:
        idea = _s(idea, 2000)
        need(idea, "ใส่ไอเดียก่อน")
        opts: dict = {k: _s((options or {}).get(k), 300) for k in OPTION_KEYS}
        opts["count"] = max(2, min(5, int((options or {}).get("count") or 3)))
        model = (options or {}).get("model") or ""
        need(not model or model in model_choices(self.models), f"โมเดล {model} ไม่อยู่ในรายการที่ทดสอบแล้ว")
        opts["model"] = model or self.model
        return self.con.execute("INSERT INTO plans(status,idea,options_json) VALUES('new',?,?)",
                                (idea, json.dumps(opts, ensure_ascii=False))).lastrowid

    def model_for(self, pid: int, override: str | None = None) -> str:
        """Call override → model chosen for this plan → config planner default."""
        if override:
            need(override in model_choices(self.models), f"โมเดล {override} ไม่อยู่ในรายการที่ทดสอบแล้ว")
            return override
        row = self.con.execute("SELECT options_json FROM plans WHERE id=?", (pid,)).fetchone()
        chosen = (json.loads(row["options_json"]) if row and row["options_json"] else {}).get("model")
        return chosen if chosen in model_choices(self.models) else self.model

    def _claude(self, pid, step, prompt, out_file: Path, turns=30, model=None):
        d = self.dir(pid)
        n = len(list((d / "logs").glob(f"{step}-*.log"))) + 1
        log = d / "logs" / f"{step}-{n}.log"
        self._set(pid, busy_step=step, pid=os.getpid(), error=None)
        if out_file.exists():
            out_file.unlink()
        try:
            model = self.model_for(pid, model)
            with (d / "logs" / "models.log").open("a") as f:
                f.write(f"{step} {log.name} {model}\n")
            result = claude_json(prompt, model=model, cwd=d, log_path=log, max_turns=turns, timeout=1800,
                                 tools="Read,Write,Edit,Bash,Glob,Grep")
        except StepError:
            try:  # charge what a failed-but-finished pass cost
                self._charge(pid, parse_claude_log(log), log)
            except (StepError, OSError):
                pass
            raise
        self._charge(pid, result, log)
        need(out_file.exists(), f"Claude did not write {out_file.name}")
        try:
            return json.loads(out_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise PlanError(f"{out_file.name} is not valid JSON: {exc}") from exc

    def _charge(self, pid, result, log):
        row = self.con.execute("SELECT usage_json,cost_usd FROM plans WHERE id=?", (pid,)).fetchone()
        usage = json.loads(row["usage_json"]) if row["usage_json"] else {}
        counted = list(usage.get("counted", []))
        if str(log) in counted:
            return
        merged = merge_usage([usage, result.get("usage", {})])
        merged["counted"] = counted + [str(log)]
        self._set(pid, usage_json=json.dumps(merged), cost_usd=round(float(row["cost_usd"] or 0) +
                                                                      float(result.get("total_cost_usd") or 0), 6))

    def _run(self, pid, status_busy, fn):
        self._set(pid, status=status_busy, error=None)
        try:
            return fn()
        except (StepError, PlanError, OSError, ValueError) as exc:
            self._set(pid, status="failed", busy_step=None, pid=None, error=str(exc)[-1500:])
            raise
        finally:
            self._set(pid, busy_step=None, pid=None)

    def _spec(self):
        return (self.root / "agents/planner.md").read_text().replace("{root}", str(self.root))

    def set_model(self, pid: int, model: str) -> dict:
        need(model in model_choices(self.models), f"โมเดล {model} ไม่อยู่ในรายการที่ทดสอบแล้ว")
        opts = self.get(pid)["options"] or {}
        opts["model"] = model
        self._set(pid, options_json=json.dumps(opts, ensure_ascii=False))
        return self.get(pid)

    def concepts(self, pid: int, extra: str = "") -> dict:
        plan = self.get(pid)
        d = self.dir(pid)
        opts = plan["options"] or {}
        count = int(opts.get("count") or 3)
        cache = d / "sources.jsonl"
        out = d / "concepts.json"
        prev = plan.get("concepts") or []
        prompt = (self._spec().replace("{cache}", str(cache)) +
                  f"\n\n# งานนี้: งาน A — คอนเซปต์ {count} แบบ\nไอเดียของผู้ใช้: {plan['idea']}\n"
                  + "".join(f"- {k}: {v}\n" for k, v in opts.items() if v and k != "count")
                  + (f"\nคำสั่งเพิ่ม/สิ่งที่อยากให้ต่างจากรอบก่อน: {_s(extra, 800)}\n" if extra else "")
                  + (f"\nรอบก่อนเสนอไปแล้ว (ห้ามซ้ำ): {', '.join(c['title'] for c in prev)}\n" if prev and extra else "")
                  + f"\nค้นของจริงด้วย lore_research อย่างน้อย 2 คำค้นก่อนเสนอ แล้วเขียนไฟล์ {out}\n")

        def work():
            items = None
            for attempt in range(2):
                data = self._claude(pid, "concepts", prompt, out)
                try:
                    items = validate_concepts(data, count)
                    break
                except PlanError as exc:
                    if attempt:
                        raise
                    prompt_fix = f"แก้ {out} ให้ผ่านการตรวจ: {exc}\nอ่าน {self.root / 'agents/planner.md'} งาน A"
                    data = self._claude(pid, "concepts-fix", prompt_fix, out, turns=12)
                    items = validate_concepts(data, count)
                    break
            self._set(pid, status="concepts_ready", concepts_json=json.dumps(items, ensure_ascii=False),
                      chosen_json=None, summary_json=None)
            return self.get(pid)
        return self._run(pid, "drafting_concepts", work)

    def choose(self, pid: int, concept: dict, notes: str = "") -> dict:
        plan = self.get(pid)
        need(plan["concepts"], "ยังไม่มีคอนเซปต์ให้เลือก")
        base = next((c for c in plan["concepts"] if c["id"] == concept.get("id")), None)
        need(base is not None, "เลือกคอนเซปต์ที่มีอยู่")
        chosen = {**base, **{k: _s(v, 1200) for k, v in concept.items() if k in base and isinstance(v, str)}}
        d = self.dir(pid)
        out = d / "summary.json"
        prompt = (self._spec().replace("{cache}", str(d / "sources.jsonl")) +
                  "\n\n# งานนี้: งาน B — สรุปคอนเซปต์ที่ผู้ใช้เลือก\n"
                  f"คอนเซปต์ที่เลือก (ผู้ใช้อาจแก้แล้ว ให้ยึดตามนี้):\n{json.dumps(chosen, ensure_ascii=False, indent=1)}\n"
                  f"ไอเดียตั้งต้น: {plan['idea']}\n" + (f"คำสั่งเพิ่มของผู้ใช้: {_s(notes, 1500)}\n" if notes else "")
                  + f"เขียนไฟล์ {out}\n")

        def work():
            summary = validate_summary(self._claude(pid, "summary", prompt, out, turns=15))
            self._set(pid, status="summary_ready", chosen_json=json.dumps(chosen, ensure_ascii=False),
                      summary_json=json.dumps(summary, ensure_ascii=False))
            return self.get(pid)
        return self._run(pid, "refining", work)

    def save_summary(self, pid: int, summary: dict) -> dict:
        plan = self.get(pid)
        need(plan["summary"], "ยังไม่มีสรุป")
        merged = validate_summary({**plan["summary"], **{k: v for k, v in summary.items() if v not in (None, "")}})
        self._set(pid, summary_json=json.dumps(merged, ensure_ascii=False))
        return self.get(pid)

    def bible(self, pid: int, opts: dict) -> dict:
        plan = self.get(pid)
        need(plan["summary"], "ต้องมีสรุปก่อนสร้าง bible")
        d = self.dir(pid)
        episodes = max(1, min(24, int(opts.get("episodes") or 6)))
        start = max(1, int(opts.get("start_episode") or 1))
        bopts = {"episodes": episodes, "start_episode": start,
                 "characters": _s(opts.get("characters"), 2000), "anchors": _s(opts.get("anchors"), 1500),
                 "notes": _s(opts.get("notes"), 1500)}
        out = d / "bible.json"
        cache = d / "sources.jsonl"
        stamp = d / "bible.parts-opts.json"
        if not stamp.exists() or json.loads(stamp.read_text() or "{}") != bopts:
            for old in d.glob("bible.part-*.json"):  # parts from another request are stale
                old.unlink()
        stamp.write_text(json.dumps(bopts, ensure_ascii=False))
        prompt = (self._spec().replace("{cache}", str(cache)) +
                  "\n\n# งานนี้: งาน C — series bible\n"
                  f"สรุปที่ผู้ใช้อนุมัติ:\n{json.dumps(plan['summary'], ensure_ascii=False, indent=1)}\n"
                  f"จำนวนตอน: {episodes} (EP.{start}–EP.{start + episodes - 1})\n"
                  + (f"ตัวละครที่ผู้ใช้อยากได้ (ใช้ตามนี้ เติมรายละเอียดที่ขาด): {bopts['characters']}\n" if bopts["characters"] else "")
                  + (f"ของจริงที่ผู้ใช้อยากให้อิง: {bopts['anchors']}\n" if bopts["anchors"] else "")
                  + (f"คำสั่งเพิ่ม: {bopts['notes']}\n" if bopts["notes"] else "")
                  + f"ค้น lore ด้วย lore_research อย่างน้อย 3 คำค้น (ไทยและอังกฤษ) ก่อนเขียน แล้วเขียนเป็นชิ้น "
                  f"{d}/bible.part-N.json และรวมด้วย merge_parts.py ให้ได้ {out} (ดูหัวข้อ ส่งงาน)\n")

        def prompt_now():
            done = existing_parts(d)
            if not done:
                return prompt
            return prompt + ("\n# ทำต่อจากรอบก่อน (รอบก่อนถูกตัดกลางทาง)\nไฟล์ที่เขียนไว้แล้วและ valid: "
                             + ", ".join(f"{n} ({what})" for n, what in done)
                             + "\nห้ามเขียนไฟล์เหล่านี้ใหม่และไม่ต้องค้น lore ซ้ำ — อ่านไฟล์เหล่านั้นแล้วเขียนเฉพาะชิ้นที่ยังขาด แล้วรวมด้วย merge_parts.py\n")

        def work():
            data = self._claude(pid, "bible", prompt_now, out, turns=45)
            try:
                bible, warns = validate_bible(data, episodes, start, cache)
            except PlanError as exc:
                fix = (f"แก้ {out} ให้ผ่านการตรวจ: {exc}\nอ่าน {self.root / 'agents/planner.md'} งาน C "
                       f"(lore ต้องใช้ URL จาก {cache} เท่านั้น)")
                bible, warns = validate_bible(self._claude(pid, "bible-fix", fix, out, turns=20), episodes, start, cache)
            self._set(pid, status="bible_ready", bible_opts_json=json.dumps(bopts, ensure_ascii=False),
                      bible_json=json.dumps(bible, ensure_ascii=False), warnings_json=json.dumps(warns, ensure_ascii=False))
            return self.get(pid)
        return self._run(pid, "drafting_bible", work)

    def save_bible(self, pid: int, bible: dict, expect_updated: str | None = None) -> dict:
        """User edit from the dashboard. Strict: nothing is dropped silently. Approved plans sync to the series."""
        plan = self.get(pid)
        need(plan["bible"], "ยังไม่มี bible")
        need(not (plan["busy_step"] and _pid_alive(plan["pid"])), "AI กำลังแก้ bible นี้อยู่ รอให้เสร็จก่อน")
        need(not expect_updated or expect_updated == plan["updated_at"],
             "bible ถูกแก้จากที่อื่นระหว่างที่คุณแก้ (เช่น AI เขียนใหม่เสร็จ) — โหลดใหม่แล้วแก้อีกครั้ง")
        eps = bible.get("episodes") or []
        need(eps, "ต้องมีอย่างน้อย 1 ตอน")
        start = int((plan["bible_opts"] or {}).get("start_episode") or plan["bible"]["episodes"][0]["number"] or 1)
        for i, e in enumerate(eps):
            e["number"] = start + i  # the editor may add/remove/reorder: keep numbers consecutive
        clean, warns = validate_bible(bible, len(eps), start, self.dir(pid) / "sources.jsonl", strict=True)
        opts = {**(plan["bible_opts"] or {}), "episodes": len(eps), "start_episode": start}
        self._set(pid, bible_json=json.dumps(clean, ensure_ascii=False), warnings_json=json.dumps(warns, ensure_ascii=False),
                  bible_opts_json=json.dumps(opts, ensure_ascii=False), error=None)
        if plan["status"] == "approved" and plan["series_slug"]:
            self._sync_series(pid, plan["series_slug"], clean)
        return self.get(pid)

    def _sync_series(self, pid: int, slug: str, bible: dict) -> None:
        row = self.con.execute("SELECT id,rules_json FROM series WHERE slug=?", (slug,)).fetchone()
        need(row is not None, f"ไม่พบซีรีส์ {slug}")
        data = json.loads(row["rules_json"]) if row["rules_json"] else {}
        data["rules"] = {"story": bible.get("story_rules", []), "visual": bible.get("visual_rules", [])}
        self.con.execute("UPDATE series SET title=?,genre=?,rules_json=?,bible_json=? WHERE id=?",
                         (bible["title"], bible.get("genre") or "", json.dumps(data, ensure_ascii=False),
                          json.dumps(bible, ensure_ascii=False), row["id"]))
        (self.root / "projects" / slug).mkdir(parents=True, exist_ok=True)
        (self.root / "projects" / slug / "bible.json").write_text(json.dumps(bible, ensure_ascii=False, indent=1))
        db.event(self.con, None, f"plan {pid}: bible edited, series {slug} updated", level="info")

    def sources(self, pid: int) -> list[dict]:
        seen, out = set(), []
        cache = self.dir(pid) / "sources.jsonl"
        if cache.exists():
            for line in cache.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("url") and r["url"] not in seen:
                    seen.add(r["url"])
                    out.append({"title": r.get("title", ""), "url": r["url"], "lang": r.get("lang", ""),
                                "extract": (r.get("extract") or "")[:400]})
        return out

    def lore_search(self, pid: int, query: str, lang: str = "th,en") -> list[dict]:
        """Real search (Wikipedia, free) that also records the hits as allowed sources for this plan."""
        query = _s(query, 120)
        need(query, "ใส่คำค้น")
        need(lang in ("th", "en", "th,en"), "ภาษาไม่ถูกต้อง")
        import subprocess
        import sys as _sys
        cache = self.dir(pid) / "sources.jsonl"
        proc = subprocess.run([_sys.executable, str(self.root / "tools/lore_research.py"), "--cache", str(cache),
                               "--lang", lang, "--limit", "4", query], capture_output=True, text=True, timeout=180)
        hits = []
        for line in proc.stdout.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("url"):
                hits.append({"title": r["title"], "url": r["url"], "lang": r.get("lang", ""),
                             "extract": (r.get("extract") or "")[:400]})
        return hits

    REGEN_KINDS = ("character", "episode", "lore", "world", "rules")

    def regen(self, pid: int, target: str, instruction: str = "", model: str | None = None) -> dict:
        """AI rewrites ONE part of the bible (or adds one: character:new / episode:new / lore:new)."""
        plan = self.get(pid)
        need(plan["bible"], "ยังไม่มี bible")
        kind, _, key = target.partition(":")
        need(kind in self.REGEN_KINDS, f"แก้ส่วน {kind} ไม่ได้")
        bible = plan["bible"]
        if kind == "character":
            need(key == "new" or any(c["id"] == key for c in bible["characters"]), f"ไม่พบตัวละคร {key}")
            need(key != "new" or len(bible["characters"]) < 8, "มีตัวละครครบ 8 ตัวแล้ว")
        elif kind == "episode":
            need(key == "new" or any(str(e["number"]) == key for e in bible["episodes"]), f"ไม่พบตอน {key}")
            need(key != "new" or len(bible["episodes"]) < 24, "มีครบ 24 ตอนแล้ว")
        elif kind == "lore":
            need(key == "new" or any(x["id"] == key for x in bible["lore"]), f"ไม่พบ lore {key}")
        d = self.dir(pid)
        out = d / "regen.json"
        cache = d / "sources.jsonl"
        shape = {
            "character": '{"character": {...ตัวละคร 1 ตัว schema เดียวกับงาน C...}}',
            "episode": '{"episode": {...ตอน 1 ตอน schema เดียวกับงาน C...}}',
            "lore": '{"lore": {...lore 1 ข้อ source_url ต้องมาจาก sources.jsonl...}}',
            "world": '{"world": {"setting": "...", "era": "...", "rules": ["..."]}, "visual_style": {...}}',
            "rules": '{"story_rules": ["..."], "visual_rules": ["..."]}'}[kind]
        what = {"character": f"ตัวละคร {key}" if key != "new" else "ตัวละครใหม่ 1 ตัว (id ใหม่ไม่ซ้ำ)",
                "episode": f"ตอน EP.{key}" if key != "new" else f"ตอนใหม่ต่อท้าย EP.{bible['episodes'][-1]['number'] + 1}",
                "lore": f"lore {key}" if key != "new" else "lore ใหม่ 1 ข้อ (ค้นด้วย lore_research ก่อน)",
                "world": "โลก/สไตล์ภาพ", "rules": "กฎเรื่องและกฎภาพ"}[kind]
        prompt = (self._spec().replace("{cache}", str(cache)) +
                  f"\n\n# งานนี้: งาน D — เขียนใหม่เฉพาะ {what} ใน series bible ที่มีอยู่\n"
                  f"bible ปัจจุบัน (ส่วนอื่นห้ามเปลี่ยน ให้สอดคล้องกับส่วนอื่นทั้งหมด):\n"
                  f"{json.dumps(bible, ensure_ascii=False)}\n"
                  + (f"คำสั่งของผู้ใช้: {_s(instruction, 1200)}\n" if instruction else "ให้ดีขึ้น ดึงคนดูมากขึ้น\n")
                  + f"เขียนไฟล์ {out} รูปแบบ {shape} (ไฟล์เล็ก เขียนครั้งเดียว) ถ้าต้องใช้ข้อเท็จจริงใหม่ให้ค้นด้วย lore_research ก่อน\n")
        if out.exists():
            out.unlink()

        def work():
            data = self._claude(pid, f"regen-{kind}", prompt, out, turns=20, model=model)
            new = json.loads(json.dumps(bible))
            if kind == "character":
                c = data.get("character") or {}
                if key == "new":
                    c["id"] = c.get("id") if c.get("id") not in {x["id"] for x in new["characters"]} else f"ch{len(new['characters']) + 1}"
                    new["characters"].append(c)
                else:
                    c["id"] = key
                    new["characters"] = [c if x["id"] == key else x for x in new["characters"]]
            elif kind == "episode":
                e = data.get("episode") or {}
                if key == "new":
                    e["number"] = new["episodes"][-1]["number"] + 1
                    new["episodes"].append(e)
                else:
                    e["number"] = int(key)
                    new["episodes"] = [e if str(x["number"]) == key else x for x in new["episodes"]]
            elif kind == "lore":
                x = data.get("lore") or {}
                if key == "new":
                    x["id"] = x.get("id") if x.get("id") not in {y["id"] for y in new["lore"]} else f"lr{len(new['lore']) + 1}"
                    new["lore"].append(x)
                else:
                    x["id"] = key
                    new["lore"] = [x if y["id"] == key else y for y in new["lore"]]
            elif kind == "world":
                new["world"] = data.get("world") or new["world"]
                new["visual_style"] = data.get("visual_style") or new["visual_style"]
            else:
                new["story_rules"] = data.get("story_rules") or new["story_rules"]
                new["visual_rules"] = data.get("visual_rules") or new["visual_rules"]
            start = int((plan["bible_opts"] or {}).get("start_episode") or bible["episodes"][0]["number"] or 1)
            for i, e in enumerate(new["episodes"]):
                e["number"] = start + i
            clean, warns = validate_bible(new, len(new["episodes"]), start, cache, strict=True)
            opts = {**(plan["bible_opts"] or {}), "episodes": len(clean["episodes"]), "start_episode": start}
            self._set(pid, bible_json=json.dumps(clean, ensure_ascii=False), warnings_json=json.dumps(warns, ensure_ascii=False),
                      bible_opts_json=json.dumps(opts, ensure_ascii=False), error=None)
            if plan["status"] == "approved" and plan["series_slug"]:
                self._sync_series(pid, plan["series_slug"], clean)
            return self.get(pid)

        # a failed rewrite keeps the bible and its status; only the error is shown
        self._set(pid, error=None)
        try:
            work()
        except (StepError, PlanError, OSError, ValueError, KeyError, TypeError) as exc:
            self._set(pid, error=f"AI เขียน {what} ใหม่ไม่สำเร็จ: {str(exc)[-600:]}")
            raise PlanError(str(exc)) from exc
        finally:
            self._set(pid, busy_step=None, pid=None)
        return self.get(pid)

    def approve(self, pid: int, slug: str | None = None) -> dict:
        """Turn the bible into a series: rules for writer/animator, bible stored on the series."""
        plan = self.get(pid)
        need(plan["bible"], "ต้องมี bible ก่อนอนุมัติ")
        bible = plan["bible"]
        slug = slugify(slug or bible["slug"] or "", "series")
        row = self.con.execute("SELECT id,rules_json,plan_id FROM series WHERE slug=?", (slug,)).fetchone()
        if row and row["plan_id"] not in (None, pid):
            raise PlanError(f"ซีรีส์ {slug} มีอยู่แล้วจากแผนอื่น ตั้ง slug ใหม่")
        data = json.loads(row["rules_json"]) if row and row["rules_json"] else {}
        data["rules"] = {"story": bible.get("story_rules", []), "visual": bible.get("visual_rules", [])}
        if row:
            self.con.execute("UPDATE series SET title=?,genre=?,rules_json=?,bible_json=?,plan_id=? WHERE id=?",
                             (bible["title"], bible.get("genre") or "", json.dumps(data, ensure_ascii=False),
                              json.dumps(bible, ensure_ascii=False), pid, row["id"]))
        else:
            self.con.execute("INSERT INTO series(slug,title,genre,rules_json,bible_json,plan_id) VALUES(?,?,?,?,?,?)",
                             (slug, bible["title"], bible.get("genre") or "", json.dumps(data, ensure_ascii=False),
                              json.dumps(bible, ensure_ascii=False), pid))
        (self.root / "projects" / slug).mkdir(parents=True, exist_ok=True)
        (self.root / "projects" / slug / "bible.json").write_text(json.dumps(bible, ensure_ascii=False, indent=1))
        self._set(pid, status="approved", series_slug=slug)
        db.event(self.con, None, f"plan {pid} approved as series {slug}", level="gate")
        return self.get(pid)
