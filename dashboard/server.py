#!/usr/bin/env python3
"""Dashboard. stdlib only; bind defaults to localhost.

SQLite is opened read-only. The only writes happen through `orchestrator.py`, started as a
detached `systemd-run --user` unit by the action endpoints (approve / reject / new / resume / cancel),
so the state machine, locks and cost accounting stay in one place.

Auth: one password in `.dashboard_pass` (repo root, chmod 600). Login sets an HMAC-signed,
HttpOnly, Secure, SameSite=Strict cookie valid 14 days. Failed logins are rate-limited per client IP
(Cloudflare `CF-Connecting-IP`). Every POST also requires the `X-ShortClip: 1` header, which a
cross-site form cannot send.
"""
from __future__ import annotations

import argparse
import hashlib
from html import escape as html_escape
import hmac
import json
import secrets
import shutil
import subprocess
import sys
import threading
import time
import mimetypes
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # the service starts in dashboard/'s parent but runs the file directly
    sys.path.insert(0, str(ROOT))
DB_PATH = Path(os.environ.get("SHORTCLIP_DB", ROOT / "db/shortclip.db"))
HTML = Path(__file__).with_name("index.html")
LOGIN_HTML = Path(__file__).with_name("login.html")
PASS_FILE = Path(os.environ.get("SHORTCLIP_DASH_PASS", ROOT / ".dashboard_pass"))
SECRET_FILE = ROOT / "db/.dashboard_secret"
COOKIE = "sc_session"
SESSION_DAYS = 14
# `Secure` cookies need https (localhost excepted in Chrome/Firefox). Behind plain http elsewhere: SHORTCLIP_INSECURE_COOKIE=1
SECURE = "" if os.environ.get("SHORTCLIP_INSECURE_COOKIE") else "Secure; "
HOOKS = {"countdown", "forbidden", "midscene", "lie"}
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _secret() -> bytes:
    if not SECRET_FILE.exists():
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_text(secrets.token_hex(32))
        SECRET_FILE.chmod(0o600)
    return SECRET_FILE.read_text().strip().encode()


def contact_email() -> str:
    """Shown on /about /privacy /terms (Google / TikTok / Meta app review want a contact). Set in config/publish.json."""
    try:
        cfg = json.loads((ROOT / "config/publish.json").read_text())
    except (OSError, ValueError):
        cfg = {}
    return html_escape(cfg.get("contact_email") or "the owner of this instance")


def password() -> str | None:
    try:
        value = PASS_FILE.read_text().strip()
    except OSError:
        return None
    return value or None


def make_token(now=None) -> str:
    exp = int((now or time.time()) + SESSION_DAYS * 86400)
    # Bind the token to the current password so changing the file logs everyone out.
    key = _secret() + (password() or "").encode()
    sig = hmac.new(key, str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def token_ok(token: str, now=None) -> bool:
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < (now or time.time()):
        return False
    key = _secret() + (password() or "").encode()
    return hmac.compare_digest(sig, hmac.new(key, exp_s.encode(), hashlib.sha256).hexdigest())


def too_many_fails(ip: str, now=None) -> bool:
    now = now or time.time()
    with _fails_lock:
        recent = [t for t in _fails.get(ip, []) if now - t < 600]
        _fails[ip] = recent
        return len(recent) >= 5


def note_fail(ip: str):
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())


def launch(args: list[str], label: str) -> str:
    """Start orchestrator.py detached so it survives dashboard restarts; returns the unit name."""
    unit = f"shortclip-{label}-{int(time.time())}"
    log = ROOT / "db" / "actions.log"
    shell = (f"echo \"== $(date -Is) {unit}: orchestrator.py {' '.join(args)}\" >> {log}; "
             f"exec \"{sys.executable}\" -u orchestrator.py \"$@\" >> {log} 2>&1")
    passthru = [f"--setenv={k}={os.environ[k]}" for k in (
        "SHORTCLIP_PUBLISH_FAKE", "SHORTCLIP_SECRETS", "SHORTCLIP_PUBLISH_CONFIG", "SHORTCLIP_PLANS", "PATH",
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CHROME", "FFMPEG", "FFPROBE", "HOME")
        if os.environ.get(k)]
    if os.environ.get("SHORTCLIP_DASH_DRYRUN") or os.environ.get("SHORTCLIP_DASH_NOLAUNCH"):
        return unit + " (dry-run)"
    if shutil.which("systemd-run") and not os.environ.get("SHORTCLIP_NO_SYSTEMD"):
        cmd = ["systemd-run", "--user", f"--unit={unit}", "--collect", *passthru,
               f"--property=WorkingDirectory={ROOT}", "/bin/bash", "-c", shell, "orchestrator",
               "--db", str(DB_PATH), *args]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if not proc.returncode:
            return unit
    # portable fallback (macOS, Docker, no user systemd): detached child that outlives the request
    log.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["/bin/bash", "-c", shell, "orchestrator", "--db", str(DB_PATH), *args], cwd=ROOT,
                     env=os.environ.copy(), start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return unit


AGENT_ROLES = [
    ("planner", "นักวางเรื่อง", "คิดคอนเซปต์ สรุป และสร้าง series bible (ตัวละคร ตอน lore)", "Claude"),
    ("writer", "นักเขียนบท", "เขียนบทพูดทีละตอนจากแผนใน bible", "Claude"),
    ("sound", "ฝ่ายเสียง", "สร้างเสียงพากย์ จัดเวลาซับทีละคำ ผสมเพลง/เอฟเฟกต์", "เครื่องมือ (ฟรี)"),
    ("director", "ผู้กำกับช็อต", "แบ่งช็อตตามเวลาเสียง ทำไฟล์ซับ", "เครื่องมือ (ฟรี)"),
    ("animator", "นักวาด", "วาดการ์ตูนทั้งตอนตามบท+เวลา แล้วระบบ render", "Claude"),
    ("editor", "ฝ่ายตัดต่อ", "รวมภาพ+เสียง ทำไฟล์ final/web", "เครื่องมือ (ฟรี)"),
    ("qa", "ฝ่ายตรวจ", "ตรวจความยาว ขนาด ความดัง ซับตรงบท", "เครื่องมือ (ฟรี)"),
]


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _claude_activity(log_path: str | None) -> list[str]:
    """Last tool calls of the Claude Code session that is running in this step's folder."""
    if not log_path:
        return []
    cwd = Path(log_path).parent.parent
    for sub in ("composition", ""):
        d = (cwd / sub) if sub else cwd
        slug = "-" + str(d).strip("/").replace("/", "-").replace(".", "-")
        proj = Path.home() / ".claude/projects" / slug
        files = sorted(proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if proj.is_dir() else []
        if files and time.time() - files[-1].stat().st_mtime < 3600:
            out = []
            for line in files[-1].read_text(errors="replace").splitlines()[-80:]:
                try:
                    m = json.loads(line).get("message", {})
                except ValueError:
                    continue
                if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
                    continue
                for c in m["content"]:
                    if c.get("type") == "tool_use":
                        inp = c.get("input") or {}
                        what = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                        out.append(f"{c['name']}: {str(what).replace(str(Path.home()), '~')[:90]}")
                    elif c.get("type") == "text" and c["text"].strip():
                        out.append("พูด: " + c["text"].strip().replace("\n", " ")[:90])
            return out[-6:]
    return []


def _quota_wait(log_dir: Path) -> str:
    """'' or a Thai note when a Claude call in this folder is sleeping on a 429."""
    for f in sorted(Path(log_dir).glob("*.wait")) if Path(log_dir).is_dir() else []:
        try:
            until = json.loads(f.read_text())["until"]
        except (OSError, ValueError, KeyError):
            continue
        if until > time.time():
            return f"โควตา Claude เต็ม (429) — รอถึง {time.strftime('%H:%M', time.localtime(until))} แล้วทำต่อเอง"
    return ""


def agents_board() -> dict:
    jobs = query("""SELECT j.*, e.number, e.variant, e.title, s.slug AS series FROM jobs j
                    JOIN episodes e ON e.id=j.episode_id JOIN series s ON s.id=e.series_id
                    WHERE j.status IN ('queued','running','waiting_gate','failed') ORDER BY j.id""")
    steps = {}
    if jobs:
        marks = ",".join("?" * len(jobs))
        for st in query(f"SELECT * FROM steps WHERE job_id IN ({marks}) ORDER BY position", [j["id"] for j in jobs]):
            steps.setdefault(st["job_id"], []).append(st)
    plans = query("SELECT id,status,busy_step,pid,idea,error,updated_at,summary_json FROM plans "
                  "WHERE status NOT IN ('approved') ORDER BY id DESC LIMIT 20") if _has_table("plans") else []
    board = []
    from shortclip.external import load_models
    models = load_models(ROOT)
    for role, name, does, engine in AGENT_ROLES:
        if engine == "Claude":
            mid = models.get(role, models["default"]).split("/")[-1].replace("claude-", "")
            name_, *ver = mid.split("-")
            engine = f"{name_.title()} {'.'.join(ver)}"
        card = {"role": role, "name": name, "does": does, "engine": engine, "state": "idle",
                "doing": "", "waiting_for": "", "needs": "", "activity": [], "items": []}
        if role == "planner":
            for pl in plans:
                ttl = (json.loads(pl["summary_json"]).get("title") if pl["summary_json"] else None) or (pl["idea"] or "")
                if pl["busy_step"] and _alive(pl["pid"]):
                    card["state"] = "working"
                    card["doing"] = {"concepts": "กำลังคิดคอนเซปต์", "concepts-fix": "กำลังแก้คอนเซปต์",
                                     "summary": "กำลังสรุปเรื่อง", "bible": "กำลังสร้าง series bible + ค้น lore",
                                     "bible-fix": "กำลังแก้ bible", "regen": "กำลังเขียนส่วนที่เลือกใน bible ใหม่"}.get(
                        pl["busy_step"], {"regen-character": "AI กำลังเขียนตัวละครใหม่", "regen-episode": "AI กำลังเขียนตอนใหม่",
                                          "regen-lore": "AI กำลังค้น+เขียน lore", "regen-world": "AI กำลังเขียนโลก/สไตล์ภาพใหม่",
                                          "regen-rules": "AI กำลังเขียนกฎใหม่"}.get(pl["busy_step"], pl["busy_step"])) + f" (แผน #{pl['id']} {ttl})"
                    card["activity"] = _claude_activity(str(PLANS_DIR / str(pl["id"]) / "logs" / "x.log"))
                    card["waiting_for"] = _quota_wait(PLANS_DIR / str(pl["id"]) / "logs")
                elif pl["status"] in ("concepts_ready", "summary_ready", "bible_ready"):
                    card["items"].append({"kind": "need", "text": {"concepts_ready": "รอคุณเลือกคอนเซปต์",
                                                                    "summary_ready": "รอคุณยืนยันสรุป แล้วสั่งสร้าง bible",
                                                                    "bible_ready": "รอคุณตรวจ bible แล้วอนุมัติเป็นซีรีส์"}[pl["status"]] + f" — แผน #{pl['id']} {ttl}",
                                          "plan": pl["id"]})
                elif pl["status"] == "failed":
                    card["items"].append({"kind": "error", "text": f"แผน #{pl['id']} ล้ม: {(pl['error'] or '')[-160:]}", "plan": pl["id"]})
                elif pl["busy_step"] and not _alive(pl["pid"]):
                    card["items"].append({"kind": "error", "text": f"แผน #{pl['id']} ค้าง (process หายไป) — กดทำใหม่", "plan": pl["id"]})
            if card["state"] != "working" and card["items"]:
                card["state"] = "needs_you" if any(i["kind"] == "need" for i in card["items"]) else "error"
        for j in jobs:
            label = f"{j['series']} EP.{j['number']}{('v' + j['variant']) if j['variant'] else ''}"
            js = steps.get(j["id"], [])
            me = next((x for x in js if x["role"] == role), None)
            if not me:
                continue
            if me["status"] == "running" and _alive(me["pid"]):
                card["state"] = "working"
                card["doing"] = f"{label}: รอบที่ {me['attempt']} เริ่ม {(me['started_at'] or '')[11:16]} (UTC)"
                if role in ("animator", "writer"):
                    card["activity"] = _claude_activity(me["log_path"])
                    if me["log_path"]:
                        card["waiting_for"] = _quota_wait(Path(me["log_path"]).parent)
            elif me["status"] == "failed":
                card["items"].append({"kind": "error", "text": f"{label} ล้ม: {(me['error'] or '')[-160:]}", "job": j["id"]})
            elif me["status"] == "queued":
                before = [x for x in js if x["position"] < me["position"] and x["status"] != "done"]
                if j["status"] == "waiting_gate" and j["gate"] == "gate1_script" and role != "writer":
                    card["items"].append({"kind": "wait", "text": f"{label}: รอคุณอนุมัติบทก่อน", "job": j["id"]})
                elif before:
                    card["items"].append({"kind": "wait", "text": f"{label}: รอ{dict((r, n) for r, n, _, _ in AGENT_ROLES)[before[-1]['role']]}ทำให้เสร็จ", "job": j["id"]})
            if role == "writer" and j["status"] == "waiting_gate" and j["gate"] == "gate1_script":
                card["items"].append({"kind": "need", "text": f"{label}: บทเสร็จแล้ว รอคุณอนุมัติ", "job": j["id"]})
            if role == "qa" and j["status"] == "waiting_gate" and j["gate"] == "gate2_final":
                card["items"].append({"kind": "need", "text": f"{label}: คลิปผ่าน QA รอคุณอนุมัติ", "job": j["id"]})
        if card["state"] == "idle" and card["items"]:
            kinds = {i["kind"] for i in card["items"]}
            card["state"] = "error" if "error" in kinds else "needs_you" if "need" in kinds else "waiting"
        board.append(card)
    return {"agents": board, "time": time.strftime("%H:%M:%S")}


def _has_table(name: str) -> bool:
    return bool(query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


PLAN_ACTIONS = {"concepts", "choose", "save-summary", "bible", "save-bible", "approve", "model", "regen", "lore-search"}


def orch(args: list[str], stdin: str | None = None, timeout=120) -> tuple[int, dict | list]:
    """Run a quick orchestrator command and parse its --json output (writes stay in the orchestrator)."""
    proc = subprocess.run([sys.executable, str(ROOT / "orchestrator.py"), "--db", str(DB_PATH), "--json", *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=timeout, input=stdin)
    out = proc.stdout.strip()
    try:
        data = json.loads(out) if out else {"error": proc.stderr.strip()[-400:]}
    except ValueError:
        data = {"error": (out or proc.stderr)[-400:]}
    return proc.returncode, data


PUB_PLATFORMS = ("youtube", "tiktok", "instagram")
PLANS_DIR = Path(os.environ.get("SHORTCLIP_PLANS") or ROOT / "plans")


def query(sql, args=()):
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def parsed(rows, *fields):
    for row in rows:
        for field in fields:
            if row.get(field):
                try:
                    row[field] = json.loads(row[field])
                except (ValueError, TypeError):
                    pass
    return rows


class Handler(BaseHTTPRequestHandler):
    server_version = "ShortClipDashboard/2.0"

    def log_message(self, format, *args):
        print(f"{self.address_string()} {format % args}")

    def json_response(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def client_ip(self) -> str:
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    def authed(self) -> bool:
        if not password():
            return False
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and token_ok(value):
                return True
        return False

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        # a whole series bible is ~50–150 KB (Thai = 3 bytes/char); everything else stays small
        limit = 600_000 if urlparse(self.path).path.endswith("/save-bible") else 20000
        if length > limit:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return json.loads(raw or b"{}")
        return {k: v[0] for k, v in parse_qs(raw.decode()).items()}

    def redirect(self, where: str, cookie: str | None = None):
        self.send_response(303)
        self.send_header("Location", where)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        try:
            path = urlparse(self.path).path.rstrip("/")
            if path == "/login":
                return self.login()
            if path == "/logout":
                return self.redirect("/login", f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; {SECURE}SameSite=Strict")
            if not self.authed():
                return self.json_response({"error": "login required"}, 401)
            if self.headers.get("X-ShortClip") != "1":
                return self.json_response({"error": "missing X-ShortClip header"}, 403)
            body = self.read_body()
            match = re.fullmatch(r"/api/job/(\d+)/(approve|reject|resume|cancel)", path)
            if match:
                return self.job_action(int(match.group(1)), match.group(2), body)
            match = re.fullmatch(r"/api/episode/(\d+)/thumb/(frames|ideas|render|select|delete)", path)
            if match:
                return self.thumb_action(int(match.group(1)), match.group(2), body)
            match = re.fullmatch(r"/api/job/(\d+)/(redo|script)", path)
            if match:
                return self.job_edit(int(match.group(1)), match.group(2), body)
            match = re.fullmatch(r"/api/job/(\d+)/voice", path)
            if match:
                return self.job_voice(int(match.group(1)), body)
            match = re.fullmatch(r"/api/series/([a-z0-9][a-z0-9-]{0,40})", path)
            if match:
                return self.series_save(match.group(1), body)
            if path == "/api/new":
                return self.new_episode(body)
            if path == "/api/plan":
                return self.plan_new(body)
            match = re.fullmatch(r"/api/publish/(youtube|tiktok|instagram)/(app|connect|finish|disconnect)", path)
            if match:
                return self.publish_account(match.group(1), match.group(2), body)
            match = re.fullmatch(r"/api/episode/(\d+)/post", path)
            if match:
                return self.publish_post(int(match.group(1)), body)
            match = re.fullmatch(r"/api/post/(\d+)/(retry|refresh)", path)
            if match:
                return self.publish_again(int(match.group(1)), match.group(2))
            if path == "/api/models":
                from shortclip.external import set_models
                try:
                    m = set_models(ROOT, {str(k): str(v) for k, v in dict(body.get("roles") or {}).items()})
                except ValueError as exc:
                    return self.json_response({"error": str(exc)}, 400)
                return self.json_response({"ok": True, "roles": {r: m.get(r) for r in m.get("roles", {})}})
            match = re.fullmatch(r"/api/plan/(\d+)/([a-z-]+)", path)
            if match and match.group(2) in PLAN_ACTIONS:
                return self.plan_action(int(match.group(1)), match.group(2), body)
            return self.json_response({"error": "not found"}, 404)
        except (sqlite3.Error, OSError, ValueError, subprocess.SubprocessError) as exc:
            return self.json_response({"error": str(exc)}, 500)

    def login(self):
        ip = self.client_ip()
        if too_many_fails(ip):
            return self.json_response({"error": "too many attempts; wait 10 minutes"}, 429)
        body = self.read_body()
        want = password()
        given = str(body.get("password", ""))
        if want and hmac.compare_digest(given.encode(), want.encode()):
            cookie = (f"{COOKIE}={make_token()}; Path=/; Max-Age={SESSION_DAYS * 86400}; "
                      f"HttpOnly; {SECURE}SameSite=Strict")
            return self.redirect("/", cookie)
        note_fail(ip)
        time.sleep(1)
        return self.redirect("/login?bad=1")

    def job_action(self, job_id: int, action: str, body: dict):
        rows = query("SELECT id,status,gate FROM jobs WHERE id=?", (job_id,))
        if not rows:
            return self.json_response({"error": "job not found"}, 404)
        job = rows[0]
        if action in {"approve", "reject"} and job["status"] != "waiting_gate":
            return self.json_response({"error": f"job is {job['status']}, not waiting at a gate"}, 409)
        if action == "resume" and job["status"] not in {"failed", "queued", "running"}:
            return self.json_response({"error": f"job is {job['status']}; nothing to resume"}, 409)
        if action == "resume" and job["status"] == "running" and self.job_has_live_pid(job_id):
            return self.json_response({"error": "job is still running"}, 409)
        if action == "cancel" and job["status"] in {"done", "cancelled"}:
            return self.json_response({"error": f"job is already {job['status']}"}, 409)
        args = ["advance", str(job_id), "--approve"] if action == "approve" else \
            ["advance", str(job_id), "--reject", (str(body.get("reason") or "").strip()[:1500] or
                                                 "ผู้ใช้ไม่อนุมัติ ให้แก้จุดที่ควรปรับปรุง")] if action == "reject" else \
            [action, str(job_id)]
        unit = launch(args, f"{action}{job_id}")
        return self.json_response({"ok": True, "unit": unit, "action": action, "job": job_id})

    def thumb_action(self, eid: int, action: str, body: dict):
        if not query("SELECT 1 FROM episodes WHERE id=?", (eid,)):
            return self.json_response({"error": "not found"}, 404)
        if action == "ideas":  # paid + slow (~1-2 min): detached
            args = ["--json", "thumb", "ideas", str(eid), "--count", str(max(2, min(6, int(body.get("count") or 5)))),
                    "--extra", str(body.get("extra") or "")[:600]]
            if body.get("model"):
                from shortclip.external import load_models, model_choices
                if body["model"] not in model_choices(load_models(ROOT)):
                    return self.json_response({"error": "โมเดลไม่อยู่ในรายการ"}, 400)
                args += ["--model", body["model"]]
            # make sure frames exist first (free, ~5 s) so the AI can look at them
            code, data = orch(["thumb", "frames", str(eid)], timeout=300)
            if code:
                return self.json_response(data, 400)
            return self.json_response({"ok": True, "unit": launch(args, f"thumb{eid}-ideas")})
        if action == "frames":
            args = ["thumb", "frames", str(eid)] + (["--force"] if body.get("force") else [])
        elif action == "render":
            args = ["thumb", "render", str(eid), "--spec-json", json.dumps(body.get("spec") or {}, ensure_ascii=False)]
        else:
            args = ["thumb", action, str(eid), str(body.get("file") or "")]
        code, data = orch(args, timeout=300)
        return self.json_response(data, 200 if not code else 400)

    def job_edit(self, job_id: int, kind: str, body: dict):
        """Edit any stage: hand-edited script, or redo writer / sound / animator. Works on cancelled,
        failed and finished jobs too (they reopen from that stage)."""
        rows = query("SELECT id,status FROM jobs WHERE id=?", (job_id,))
        if not rows:
            return self.json_response({"error": "job not found"}, 404)
        if rows[0]["status"] == "running" and self.job_has_live_pid(job_id):
            return self.json_response({"error": "agent กำลังทำตอนนี้อยู่ รอให้เสร็จก่อน"}, 409)
        if kind == "script":
            if not isinstance(body.get("script"), dict):
                return self.json_response({"error": "missing script"}, 400)
            if os.environ.get("SHORTCLIP_DASH_DRYRUN"):
                return self.json_response({"ok": True, "dry_run": "edit-script"})
            code, data = orch(["edit-script", str(job_id), "--no-run"],
                              stdin=json.dumps(body["script"], ensure_ascii=False))
            if code:
                return self.json_response(data, 400)
            unit = launch(["resume", str(job_id)], f"edit{job_id}") if data.get("rerun") else None
            return self.json_response({"ok": True, "rerun": data.get("rerun"), "same_layout": data.get("same_layout"),
                                       "changed_beats": data.get("changed_beats"), "unit": unit})
        frm = str(body.get("from") or "")
        if frm not in ("writer", "sound", "animator"):
            return self.json_response({"error": "เลือกขั้นที่จะทำใหม่"}, 400)
        args = ["redo", str(job_id), "--from", frm, "--reason", str(body.get("reason") or "").strip()[:1500]]
        if body.get("fresh"):
            args.append("--fresh")
        if "beats" in body and body["beats"] is not None:
            beats = [b for b in body["beats"] if b in ("hook", "setup", "escalate", "twist", "cliff")]
            args += ["--beats", ",".join(beats)]
        if frm == "animator" and not body.get("fresh") and not str(body.get("reason") or "").strip():
            return self.json_response({"error": "บอกว่าอยากให้แก้ภาพตรงไหน หรือเลือกวาดใหม่ทั้งหมด"}, 400)
        # Validate synchronously (cheap), then let the long run continue detached.
        code, data = orch([*args, "--no-run"])
        if code:
            return self.json_response(data, 400)
        unit = launch(["resume", str(job_id)], f"redo{job_id}-{frm}")
        return self.json_response({"ok": True, "unit": unit, "from": frm})

    @staticmethod
    def job_has_live_pid(job_id: int) -> bool:
        for row in query("SELECT pid FROM steps WHERE job_id=? AND status='running' AND pid IS NOT NULL", (job_id,)):
            try:
                os.kill(int(row["pid"]), 0)
                return True
            except (OSError, ValueError):
                continue
        return False

    def job_voice(self, job_id: int, body: dict):
        from shortclip.pipeline import clean_voice
        rows = query("SELECT id,status FROM jobs WHERE id=?", (job_id,))
        if not rows:
            return self.json_response({"error": "job not found"}, 404)
        if rows[0]["status"] in {"running", "cancelled"}:
            return self.json_response({"error": f"job is {rows[0]['status']}"}, 409)
        try:
            cfg = clean_voice(body.get("voice") or body)
        except Exception as exc:  # StepError
            return self.json_response({"error": str(exc)}, 400)
        unit = launch(["voice", str(job_id), "--engine", cfg["engine"], "--voice", cfg["voice"],
                       "--speed", str(cfg["speed"])], f"voice{job_id}")
        return self.json_response({"ok": True, "unit": unit, "voice": cfg})

    def series_save(self, slug: str, body: dict):
        from shortclip.pipeline import clean_rules, clean_voice
        try:
            args = ["series", slug, "--rules-json", json.dumps(clean_rules(body.get("rules")), ensure_ascii=False)]
            if body.get("voice"):
                args += ["--voice-json", json.dumps(clean_voice(body["voice"]))]
        except Exception as exc:
            return self.json_response({"error": str(exc)}, 400)
        for key in ("title", "genre"):
            if body.get(key):
                args += [f"--{key}", str(body[key])[:120]]
        if os.environ.get("SHORTCLIP_DASH_DRYRUN"):
            return self.json_response({"ok": True, "dry_run": args})
        # Small and synchronous: the user expects the saved rules on the next page load.
        proc = subprocess.run([sys.executable, str(ROOT / "orchestrator.py"), "--db", str(DB_PATH), *args],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        if proc.returncode:
            return self.json_response({"error": proc.stderr.strip()[-400:] or "save failed"}, 500)
        return self.json_response({"ok": True, "series": json.loads(proc.stdout)})

    def oauth_page(self, ok: bool, name: str, msg: str):
        import html as _html
        body = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                f"<title>{name}</title><body style='background:#0b0e14;color:#e8edf5;font:17px system-ui;padding:40px'>"
                f"<h2>{'✓' if ok else '✗'} {name}</h2><p>{_html.escape(msg)[:400]}</p>"
                f"<p><a style='color:#73a7ff' href='/#accounts'>กลับไปหน้า dashboard</a></p>").encode()
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")  # the URL carried a one-time code
        self.end_headers()
        self.wfile.write(body)

    def publish_account(self, platform: str, action: str, body: dict):
        if os.environ.get("SHORTCLIP_DASH_DRYRUN"):
            return self.json_response({"ok": True, "dry_run": [platform, action]})
        if action == "app":  # credentials go through stdin, never argv/logs
            allowed = {"youtube": ("client_id", "client_secret"), "tiktok": ("client_key", "client_secret"),
                       "instagram": ("app_id", "app_secret")}[platform]
            payload = {k: str(body.get(k) or "")[:500] for k in allowed}
            code, data = orch(["publish", "set-app", platform], stdin=json.dumps(payload))
        elif action == "finish":
            code, data = orch(["publish", "finish", platform, "--pasted-url", str(body.get("pasted_url") or "")[:3000]])
        else:
            code, data = orch(["publish", action, platform])
        return self.json_response(data, 200 if not code else 400)

    def publish_post(self, eid: int, body: dict):
        platform = str(body.get("platform") or "")
        if platform not in PUB_PLATFORMS:
            return self.json_response({"error": "เลือกแพลตฟอร์ม"}, 400)
        if body.get("confirm") is not True:
            return self.json_response({"error": "ต้องยืนยันก่อนโพสต์"}, 400)
        opts = body.get("options") or {}
        if os.environ.get("SHORTCLIP_DASH_DRYRUN"):
            return self.json_response({"ok": True, "dry_run": {"platform": platform, "options": opts}})
        args = ["publish", "new", str(eid), platform, "--options-json", json.dumps(opts, ensure_ascii=False)]
        if body.get("again"):
            args.append("--again")
        code, data = orch(args)
        if code:
            return self.json_response(data, 400)
        unit = launch(["--json", "publish", "run", str(data["id"])], f"post{data['id']}-{platform}")
        return self.json_response({"ok": True, "post": data, "unit": unit})

    def publish_again(self, post_id: int, action: str):
        if action == "refresh":
            code, data = orch(["publish", "refresh", str(post_id)])
            return self.json_response(data, 200 if not code else 400)
        rows = query("SELECT status,pid FROM posts WHERE id=?", (post_id,))
        if not rows or rows[0]["status"] != "failed":
            return self.json_response({"error": "ลองใหม่ได้เฉพาะโพสต์ที่ล้ม"}, 400)
        unit = launch(["--json", "publish", "run", str(post_id)], f"post{post_id}-retry")
        return self.json_response({"ok": True, "unit": unit})

    def plan_new(self, body: dict):
        from shortclip.planning import OPTION_KEYS
        idea = str(body.get("idea") or "").strip()[:2000]
        if not idea:
            return self.json_response({"error": "ใส่ไอเดียก่อน"}, 400)
        opts = {k: str(body.get(k) or "").strip()[:300] for k in OPTION_KEYS}
        opts["count"] = max(2, min(5, int(body.get("count") or 3)))
        if body.get("model"):
            from shortclip.external import load_models, model_choices
            if body["model"] not in model_choices(load_models(ROOT)):
                return self.json_response({"error": "โมเดลไม่อยู่ในรายการ"}, 400)
            opts["model"] = body["model"]
        if os.environ.get("SHORTCLIP_DASH_DRYRUN"):
            return self.json_response({"ok": True, "dry_run": opts})
        proc = subprocess.run([sys.executable, str(ROOT / "orchestrator.py"), "--db", str(DB_PATH), "--json", "plan", "new",
                               "--idea", idea, "--options-json", json.dumps(opts, ensure_ascii=False)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        if proc.returncode:
            return self.json_response({"error": proc.stdout.strip()[-300:] or proc.stderr.strip()[-300:]}, 400)
        pid = json.loads(proc.stdout)["id"]
        unit = launch(["--json", "plan", "concepts", str(pid)], f"plan{pid}-concepts")
        return self.json_response({"ok": True, "plan": pid, "unit": unit})

    def plan_action(self, pid: int, action: str, body: dict):
        rows = query("SELECT status,busy_step,pid FROM plans WHERE id=?", (pid,))
        if not rows:
            return self.json_response({"error": "plan not found"}, 404)
        if rows[0]["busy_step"] and _alive(rows[0]["pid"]):
            return self.json_response({"error": "agent กำลังทำงานกับแผนนี้อยู่ รอให้เสร็จก่อน"}, 409)
        if action == "save-bible":  # big body: through stdin
            if os.environ.get("SHORTCLIP_DASH_DRYRUN"):
                return self.json_response({"ok": True, "dry_run": "save-bible"})
            args = ["plan", "save-bible", str(pid), "--bible-json", "-"]
            if body.get("expect_updated"):
                args += ["--expect-updated", str(body["expect_updated"])]
            code, data = orch(args, stdin=json.dumps(body.get("bible") or {}, ensure_ascii=False))
            return self.json_response({"ok": True, "plan": data} if not code else data, 200 if not code else 400)
        if action == "lore-search":
            code, data = orch(["plan", "lore-search", str(pid), str(body.get("query") or "")[:120],
                               "--lang", str(body.get("lang") or "th,en")], timeout=200)
            return self.json_response(data, 200 if not code else 400)
        if action == "regen":
            target = str(body.get("target") or "")
            if not re.fullmatch(r"(character:[A-Za-z0-9_-]{1,10}|episode:(\d{1,3}|new)|lore:[A-Za-z0-9_-]{1,10}|world|rules)", target):
                return self.json_response({"error": "เลือกส่วนที่จะให้ AI เขียนใหม่"}, 400)
            args = ["--json", "plan", "regen", str(pid), target, "--instruction", str(body.get("instruction") or "")[:1200]]
            if body.get("model"):
                from shortclip.external import load_models, model_choices
                if body["model"] not in model_choices(load_models(ROOT)):
                    return self.json_response({"error": "โมเดลไม่อยู่ในรายการ"}, 400)
                args += ["--model", body["model"]]
            unit = launch(args, f"plan{pid}-regen")
            return self.json_response({"ok": True, "unit": unit})
        quick = {"save-summary": "--summary-json"}
        if action in quick or action in ("approve", "model"):
            args = ["--json", "plan", action, str(pid)]
            if action == "model":
                args.append(str(body.get("model") or ""))
            elif action in quick:
                args += [quick[action], json.dumps(body.get(action.split("-")[1]) or {}, ensure_ascii=False)]
            elif body.get("slug"):
                args += ["--slug", str(body["slug"])[:40]]
            if os.environ.get("SHORTCLIP_DASH_DRYRUN"):
                return self.json_response({"ok": True, "dry_run": args})
            proc = subprocess.run([sys.executable, str(ROOT / "orchestrator.py"), "--db", str(DB_PATH), *args],
                                  cwd=ROOT, capture_output=True, text=True, timeout=60)
            out = proc.stdout.strip()
            if proc.returncode:
                return self.json_response({"error": (json.loads(out).get("error") if out.startswith("{") else out) or proc.stderr[-300:]}, 400)
            return self.json_response({"ok": True, "plan": json.loads(out)})
        if body.get("model") and action in ("concepts", "choose", "bible") and not os.environ.get("SHORTCLIP_DASH_DRYRUN"):
            proc = subprocess.run([sys.executable, str(ROOT / "orchestrator.py"), "--db", str(DB_PATH), "--json", "plan", "model",
                                   str(pid), str(body["model"])], cwd=ROOT, capture_output=True, text=True, timeout=60)
            if proc.returncode:
                return self.json_response({"error": "โมเดลไม่อยู่ในรายการ"}, 400)
        if action == "concepts":
            args = ["--json", "plan", "concepts", str(pid), "--extra", str(body.get("extra") or "")[:800]]
        elif action == "choose":
            args = ["--json", "plan", "choose", str(pid), "--concept-json", json.dumps(body.get("concept") or {}, ensure_ascii=False),
                    "--notes", str(body.get("notes") or "")[:1500]]
        else:  # bible
            args = ["--json", "plan", "bible", str(pid), "--opts-json", json.dumps(body.get("opts") or {}, ensure_ascii=False)]
        unit = launch(args, f"plan{pid}-{action}")
        return self.json_response({"ok": True, "unit": unit})

    def new_episode(self, body: dict):
        series = str(body.get("series") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", series):
            return self.json_response({"error": "series must be a slug like zombie-school"}, 400)
        try:
            number = int(body.get("ep"))
        except (TypeError, ValueError):
            return self.json_response({"error": "ep must be a number"}, 400)
        if not 1 <= number <= 999:
            return self.json_response({"error": "ep must be 1..999"}, 400)
        hook = str(body.get("hook") or "countdown")
        if hook not in HOOKS:
            return self.json_response({"error": f"hook must be one of {sorted(HOOKS)}"}, 400)
        variant = str(body.get("variant") or "").strip()
        if variant and not re.fullmatch(r"[a-z0-9]{1,8}", variant):
            return self.json_response({"error": "variant must be short letters/digits"}, 400)
        exists = query("""SELECT 1 FROM episodes e JOIN series s ON s.id=e.series_id
                          WHERE s.slug=? AND e.number=? AND e.variant=?""", (series, number, variant))
        if exists:
            return self.json_response({"error": f"{series} EP.{number}{variant} already exists"}, 409)
        idea = str(body.get("idea") or "").strip()[:1500]
        args = ["new", "--series", series, "--ep", str(number), "--hook", hook, "--requested-by", "dashboard"]
        from shortclip.pipeline import clean_rules, clean_voice
        try:
            if body.get("voice"):
                args += ["--voice-json", json.dumps(clean_voice(body["voice"]))]
            if body.get("rules"):
                args += ["--rules-json", json.dumps(clean_rules(body["rules"]), ensure_ascii=False)]
        except Exception as exc:
            return self.json_response({"error": str(exc)}, 400)
        if body.get("target_seconds"):
            t = int(body["target_seconds"])
            if not 50 <= t <= 70:
                return self.json_response({"error": "target_seconds must be 50..70"}, 400)
            args += ["--target-seconds", str(t)]
        if body.get("models"):
            from shortclip.external import load_models, model_choices
            ok = model_choices(load_models(ROOT))
            ms = {r: m for r, m in dict(body["models"]).items() if r in ("writer", "animator") and m}
            if any(m not in ok for m in ms.values()):
                return self.json_response({"error": "โมเดลไม่อยู่ในรายการ"}, 400)
            if ms:
                args += ["--models-json", json.dumps(ms)]
        if variant:
            args += ["--variant", variant]
        if idea:
            args += ["--idea", idea]
        unit = launch(args, f"new-{series}-{number}")
        return self.json_response({"ok": True, "unit": unit})

    def file_response(self, path: Path):
        return self.html_response(path.read_bytes())

    def html_response(self, raw):
        raw = raw.encode() if isinstance(raw, str) else raw
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; media-src 'self'; frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            if path == "/api/health":
                return self.json_response({"ok": True, "auth": bool(password())})
            if path == "/login":
                return self.file_response(LOGIN_HTML)
            if path in ("/about", "/privacy", "/terms"):  # public pages required by Google / TikTok / Meta app settings
                page = (Path(__file__).with_name("public") / f"{path[1:]}.html").read_text()
                return self.html_response(page.replace("{{CONTACT_EMAIL}}", contact_email()))
            match = re.fullmatch(r"/oauth/(tiktok|instagram)/callback", path)
            if match:  # comes from the platform: no session cookie (SameSite=Strict); the one-time state guards it
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                name = {"tiktok": "TikTok", "instagram": "Instagram"}[match.group(1)]
                if q.get("error"):
                    return self.oauth_page(False, name, q.get("error_description") or q["error"])
                code, data = orch(["publish", "finish", match.group(1), "--state", q.get("state", ""),
                                   "--code", q.get("code", "")])
                if code:
                    return self.oauth_page(False, name, str(data.get("error", "failed")))
                acct = (data.get("account") or {}).get("name", "")
                return self.oauth_page(True, name, f"เชื่อมบัญชี {acct} แล้ว")
            match = re.fullmatch(r"/pub/([A-Za-z0-9_\-=]+\.[0-9a-f]{40})/video\.mp4", path)
            if match:  # signed, short-lived link so Instagram can fetch one finished video
                from shortclip.publish import check_media
                found = check_media(ROOT, match.group(1))
                if not found:
                    return self.json_response({"error": "forbidden"}, 403)
                return self.media(str(found), allow_public=True)
            if path in ("/app.js", "/app.css", "/publish.js", "/bible_edit.js", "/edit_stages.js", "/thumbs.js") and self.authed():
                f = Path(__file__).with_name(path.lstrip("/"))
                raw = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8" if path.endswith(".js")
                                 else "text/css; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
            if not self.authed():
                if path == "/" or not path.startswith(("/api", "/media")):
                    return self.redirect("/login")
                return self.json_response({"error": "login required"}, 401)
            if path == "/":
                return self.file_response(HTML)
            match = re.fullmatch(r"/api/episode/(\d+)/script", path)
            if match:
                rows = query("SELECT script_json FROM episodes WHERE id=?", (int(match.group(1)),))
                if not rows:
                    return self.json_response({"error": "not found"}, 404)
                return self.json_response(json.loads(rows[0]["script_json"] or "null"))
            match = re.fullmatch(r"/api/series/([a-z0-9][a-z0-9-]{0,40})", path)
            if match:
                rows = query("SELECT slug,title,genre,rules_json,bible_json FROM series WHERE slug=?", (match.group(1),))
                if not rows:
                    return self.json_response({"slug": match.group(1), "rules": {"story": [], "visual": []}, "voice": None})
                data = json.loads(rows[0]["rules_json"] or "{}")
                return self.json_response({"slug": rows[0]["slug"], "title": rows[0]["title"], "genre": rows[0]["genre"],
                                           "rules": data.get("rules") or {"story": [], "visual": []},
                                           "voice": data.get("voice"),
                                           "bible": json.loads(rows[0]["bible_json"]) if rows[0]["bible_json"] else None})
            if path == "/api/models":
                from shortclip.external import load_models
                m = load_models(ROOT)
                return self.json_response({"roles": {r: {"label": lbl, "model": m.get(r, m["default"])}
                                                     for r, lbl in m.get("roles", {}).items()},
                                           "choices": m.get("choices", [])})
            if path == "/api/publish/accounts":
                code, data = orch(["publish", "accounts"])
                return self.json_response(data, 200 if not code else 400)
            if path == "/api/publish/creator":
                code, data = orch(["publish", "creator"])
                return self.json_response(data, 200 if not code else 400)
            match = re.fullmatch(r"/api/episode/(\d+)/posts", path)
            if match:
                eid = int(match.group(1))
                rows = query("SELECT * FROM posts WHERE episode_id=? ORDER BY id DESC", (eid,)) if _has_table("posts") else []
                for r in rows:
                    r["options"] = json.loads(r.pop("options_json") or "null")
                    r.pop("response_json", None)
                    r["alive"] = r["status"] in ("uploading", "processing") and _alive(r.get("pid"))
                code, defaults = orch(["publish", "defaults", str(eid)])
                return self.json_response({"posts": rows, "defaults": defaults if not code else {}})
            if path == "/api/agents":
                return self.json_response(agents_board())
            if path == "/api/plans":
                return self.json_response(query("""SELECT id,status,busy_step,series_slug,cost_usd,updated_at,
                                                   substr(idea,1,140) AS idea,
                                                   json_extract(summary_json,'$.title') AS title
                                                   FROM plans ORDER BY id DESC LIMIT 50""") if _has_table("plans") else [])
            match = re.fullmatch(r"/api/plan/(\d+)/sources", path)
            if match:
                code, data = orch(["plan", "sources", match.group(1)])
                return self.json_response(data, 200 if not code else 400)
            match = re.fullmatch(r"/api/plan/(\d+)", path)
            if match:
                rows = query("SELECT * FROM plans WHERE id=?", (int(match.group(1)),))
                if not rows:
                    return self.json_response({"error": "not found"}, 404)
                p = rows[0]
                for k in [k for k in p if k.endswith("_json")]:
                    p[k[:-5]] = json.loads(p.pop(k)) if p.get(k) else None
                p["alive"] = bool(p.get("busy_step")) and _alive(p.get("pid"))
                p["quota_wait"] = _quota_wait(PLANS_DIR / str(p["id"]) / "logs") if p["alive"] else ""
                return self.json_response(p)
            if path == "/api/voices":
                from shortclip.pipeline import VOICE_ENGINES
                samples = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "projects").glob("*/*/voice_test/*.mp3"))
                # Wayu (local TTS, non-commercial licence) is offered only when installed
                WAYU_DIR = os.environ.get("WAYU_DIR") or os.path.join(os.path.expanduser("~"), ".local/share/wayu-tts")
                engines = {k: list(v) for k, v in VOICE_ENGINES.items() if k != "wayu" or os.path.isdir(WAYU_DIR)}
                return self.json_response({"engines": engines,
                                           "samples": samples})
            match = re.fullmatch(r"/api/episode/(\d+)/thumb", path)
            if match:
                code, data = orch(["thumb", "state", match.group(1)])
                if not code:
                    epdir = self.episode_dir_of(int(match.group(1)))
                    data["base"] = str((epdir / "thumbs").relative_to(ROOT)) if epdir else ""
                return self.json_response(data, 200 if not code else 400)
            match = re.fullmatch(r"/api/episode/(\d+)/files", path)
            if match:
                return self.json_response(self.episode_files(int(match.group(1))))
            if path == "/api/series":
                return self.json_response(query("""SELECT s.id, s.slug, COALESCE(MAX(e.number),0) AS last_ep
                                                   FROM series s LEFT JOIN episodes e ON e.series_id=s.id
                                                   GROUP BY s.id ORDER BY s.slug"""))
            if path == "/api/jobs":
                rows = query("""SELECT j.id,j.kind,j.status,j.gate,j.current_step,j.error,j.created_at,
                                e.id AS episode_id,e.number,e.variant,e.title,s.slug AS series
                                FROM jobs j LEFT JOIN episodes e ON e.id=j.episode_id
                                LEFT JOIN series s ON s.id=e.series_id ORDER BY j.id DESC LIMIT 100""")
                return self.json_response(rows)
            if path == "/api/episodes":
                return self.json_response(query("SELECT * FROM v_episode_cost ORDER BY series_id,number,variant"))
            match = re.fullmatch(r"/api/episode/(\d+)", path)
            if match:
                eid = int(match.group(1))
                episode = query("SELECT * FROM v_episode_cost WHERE episode_id=?", (eid,))
                if not episode:
                    return self.json_response({"error": "not found"}, 404)
                jobs = query("SELECT * FROM jobs WHERE episode_id=? ORDER BY id DESC", (eid,))
                job_ids = [j["id"] for j in jobs]
                steps, events = [], []
                if job_ids:
                    marks = ",".join("?" * len(job_ids))
                    steps = query(f"SELECT * FROM steps WHERE job_id IN ({marks}) ORDER BY job_id,position,id", job_ids)
                    events = query(f"SELECT * FROM events WHERE job_id IN ({marks}) ORDER BY id DESC LIMIT 100", job_ids)
                artifacts = query("SELECT * FROM artifacts WHERE episode_id=? ORDER BY id", (eid,))
                return self.json_response({"episode": episode[0], "jobs": jobs,
                                           "steps": parsed(steps, "usage_json", "output_json"),
                                           "events": parsed(events, "payload_json"),
                                           "artifacts": parsed(artifacts, "meta_json")})
            if path == "/api/sounds":
                kind = parse_qs(url.query).get("kind", [""])[0]
                if kind:
                    rows = query("SELECT * FROM sounds WHERE kind=? ORDER BY name LIMIT 500", (kind,))
                else:
                    rows = query("SELECT * FROM sounds ORDER BY kind,name LIMIT 500")
                return self.json_response(rows)
            if path == "/api/events":
                return self.json_response(query("SELECT * FROM events ORDER BY id DESC LIMIT 50"))
            if path == "/media":
                return self.media(parse_qs(url.query).get("path", [""])[0])
            return self.json_response({"error": "not found"}, 404)
        except (sqlite3.Error, OSError, ValueError) as exc:
            return self.json_response({"error": str(exc)}, 500)

    @staticmethod
    def episode_dir_of(eid: int):
        jobs = query("SELECT params_json FROM jobs WHERE episode_id=? ORDER BY id DESC LIMIT 1", (eid,))
        if jobs and jobs[0]["params_json"]:
            p = Path(json.loads(jobs[0]["params_json"]).get("epdir", ""))
            if p.is_dir() and (ROOT / "projects").resolve() in p.resolve().parents:
                return p.resolve()
        return None

    @staticmethod
    def episode_files(eid: int) -> dict:
        jobs = query("SELECT params_json FROM jobs WHERE episode_id=? ORDER BY id DESC LIMIT 1", (eid,))
        epdir = None
        if jobs and jobs[0]["params_json"]:
            epdir = Path(json.loads(jobs[0]["params_json"]).get("epdir", ""))
        if not epdir or not epdir.is_dir():
            rows = query("""SELECT s.slug,e.number,e.variant FROM episodes e JOIN series s ON s.id=e.series_id
                            WHERE e.id=?""", (eid,))
            if rows:
                r = rows[0]
                epdir = ROOT / "projects" / r["slug"] / f"ep{r['number']}{('v' + r['variant']) if r['variant'] else ''}"
        out = {"voice": None, "duration": None, "lines": [], "mix": None, "web": None, "final": None, "qa": None}
        if not epdir or not epdir.is_dir():
            return out
        rel = lambda p: str(p.relative_to(ROOT)) if p.exists() else None  # noqa: E731
        for name in ("voice.json", "audio/beats/_voice.json"):
            if (epdir / name).exists():
                out["voice"] = json.loads((epdir / name).read_text())
                break
        if (epdir / "shots.json").exists():
            shots = json.loads((epdir / "shots.json").read_text())
            out["duration"] = shots.get("duration")
            out["voice"] = out["voice"] or shots.get("voice")
            out["lines"] = [{"id": s["line"], "start": s["start"], "end": s["end"], "text": s["subtitle"],
                             "words": len(s.get("words") or [])} for s in shots.get("shots", [])]
        out["mix"] = rel(epdir / "audio/mix.wav")
        out["web"] = rel(epdir / "renders/web.mp4")
        out["final"] = rel(epdir / "renders/final.mp4")
        if (epdir / "qa.json").exists():
            qa = json.loads((epdir / "qa.json").read_text())
            out["qa"] = {"passed": qa.get("passed"), "fails": qa.get("fails"), "warns": qa.get("warns"),
                         "lufs": (qa.get("audio") or {}).get("lufs"), "duration": (qa.get("video") or {}).get("duration")}
        return out

    def media(self, raw, allow_public=False):
        # Only finished project media can be read; no arbitrary local-file endpoint.
        candidate = (ROOT / raw.lstrip("/")).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        projects = (ROOT / "projects").resolve()
        if projects not in candidate.parents or candidate.suffix.lower() not in {".mp4", ".wav", ".mp3", ".srt", ".png", ".jpg"}:
            return self.json_response({"error": "forbidden"}, 403)
        if not candidate.is_file():
            return self.json_response({"error": "not found"}, 404)
        size = candidate.stat().st_size
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match:
                return self.json_response({"error": "bad range"}, 416)
            if match.group(1):
                start = int(match.group(1))
                end = min(int(match.group(2)) if match.group(2) else end, end)
            else:
                start = max(0, size - int(match.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with candidate.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("SHORTCLIP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("SHORTCLIP_PORT", "8099")))
    args = ap.parse_args()
    if not DB_PATH.exists():
        from shortclip import db as _db
        _con = _db.connect(DB_PATH)
        _db.init_db(_con)
        _con.close()
    if not password():
        PASS_FILE.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(12)
        PASS_FILE.write_text(generated)
        PASS_FILE.chmod(0o600)
        print(f"First run: dashboard password generated and saved to {PASS_FILE}\n  password: {generated}", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ShortClip dashboard: http://{args.host}:{args.port} db={DB_PATH}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
