"""Post finished episodes to YouTube (Shorts), TikTok (inbox draft or direct post) and Instagram (Reels).

Nothing is posted without a user click: the dashboard creates a `posts` row from the user's choices
(caption, privacy, toggles) and starts `orchestrator.py publish run <id>` detached.

Secrets live in `secrets/publish/*.json` (dir 0700, files 0600, gitignored). App credentials are
written from stdin (never argv), tokens only by the OAuth callbacks; the API never returns them.

Endpoints follow the platform docs (Nov 2026):
- YouTube Data API v3 resumable upload (videos.insert), OAuth installed client + PKCE, paste-back flow.
- TikTok Content Posting API: /v2/post/publish/inbox/video/init/ (video.upload) and
  /v2/post/publish/video/init/ (video.publish) with FILE_UPLOAD chunks, creator_info, status/fetch.
  Unaudited TikTok apps may only Direct Post as SELF_ONLY to a private account.
- Instagram API with Instagram Login: REELS container from a signed public video_url, status poll,
  media_publish. Long-lived token (60 d) refreshed when older than a day.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import time
import urllib.parse
from pathlib import Path

import requests

PLATFORMS = ("youtube", "tiktok", "instagram")
SCOPES = {
    "youtube": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
    "tiktok": "user.info.basic,video.upload,video.publish",
    "instagram": "instagram_business_basic,instagram_business_content_publish",
}
REAL = {
    "google_auth": "https://accounts.google.com/o/oauth2/v2/auth",
    "google_token": "https://oauth2.googleapis.com/token",
    "yt_upload": "https://www.googleapis.com/upload/youtube/v3/videos",
    "yt_api": "https://www.googleapis.com/youtube/v3",
    "yt_thumb": "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
    "tt_auth": "https://www.tiktok.com/v2/auth/authorize/",
    "tt_api": "https://open.tiktokapis.com",
    "ig_auth": "https://www.instagram.com/oauth/authorize",
    "ig_token": "https://api.instagram.com/oauth/access_token",
    "ig_graph": "https://graph.instagram.com",
}
GOOGLE_REDIRECT = "http://localhost:1"
TT_PRIVACY = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")
YT_PRIVACY = ("private", "unlisted", "public")
MIB = 1024 * 1024


class PublishError(RuntimeError):
    pass


def need(cond, msg):
    if not cond:
        raise PublishError(msg)


def url(key: str) -> str:
    """Real endpoint, or the fake server in tests (SHORTCLIP_PUBLISH_FAKE=http://127.0.0.1:port)."""
    fake = os.environ.get("SHORTCLIP_PUBLISH_FAKE")
    return f"{fake.rstrip('/')}/{key}" if fake else REAL[key]


# ---------------------------------------------------------------- secrets

class Vault:
    def __init__(self, root: Path):
        override = os.environ.get("SHORTCLIP_SECRETS")  # tests / selftest keep their tokens elsewhere
        self.dir = Path(override) if override else Path(root) / "secrets" / "publish"

    def _ensure(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir.parent, 0o700)
        os.chmod(self.dir, 0o700)

    def load(self, name: str) -> dict:
        p = self.dir / f"{name}.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save(self, name: str, data: dict) -> None:
        self._ensure()
        p = self.dir / f"{name}.json"
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        tmp.replace(p)

    def media_key(self) -> bytes:
        p = self.dir / "media_key"
        if not p.exists():
            self._ensure()
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
        return p.read_text().strip().encode()


def sign_media(root: Path, rel_path: str, ttl: int = 7200, now=None) -> str:
    """Token for /pub/<token>: lets Instagram fetch ONE finished video for a short time without login."""
    body = base64.urlsafe_b64encode(json.dumps({"p": rel_path, "e": int((now or time.time()) + ttl)}).encode()).decode()
    sig = hmac.new(Vault(root).media_key(), body.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{body}.{sig}"


def check_media(root: Path, token: str, now=None) -> Path | None:
    try:
        body, sig = token.split(".", 1)
        good = hmac.new(Vault(root).media_key(), body.encode(), hashlib.sha256).hexdigest()[:40]
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
    except (ValueError, TypeError):
        return None
    if data.get("e", 0) < (now or time.time()):
        return None
    path = (Path(root) / data["p"]).resolve()
    projects = (Path(root) / "projects").resolve()
    if projects not in path.parents or path.suffix.lower() != ".mp4" or not path.is_file():
        return None
    return path


# ---------------------------------------------------------------- http helpers

def _json(resp: requests.Response, what: str) -> dict:
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text[:500]}
    if resp.status_code >= 400:
        raise PublishError(f"{what}: HTTP {resp.status_code} {json.dumps(data, ensure_ascii=False)[:600]}")
    return data


def _tt(resp: requests.Response, what: str) -> dict:
    """TikTok wraps every answer as {data, error:{code}}; code != ok is a failure even on HTTP 200."""
    try:
        data = resp.json()
    except ValueError:
        raise PublishError(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")
    code = (data.get("error") or {}).get("code", "ok")
    if resp.status_code >= 400 or code != "ok":
        msg = (data.get("error") or {}).get("message", "")
        hint = {
            "unaudited_client_can_only_post_to_private_accounts":
                "แอป TikTok ยังไม่ผ่าน audit: ตั้งบัญชีเป็นส่วนตัว แล้วเลือก 'เฉพาะฉัน' หรือใช้โหมด Inbox",
            "spam_risk_too_many_posts": "TikTok จำกัดจำนวนโพสต์ต่อวันแล้ว ลองพรุ่งนี้",
            "spam_risk_too_many_pending_share": "มีดราฟต์ค้างใน Inbox เกิน 5 อันใน 24 ชม. ไปโพสต์/ลบในแอปก่อน",
            "access_token_invalid": "token หมดอายุ — เชื่อมบัญชี TikTok ใหม่",
            "scope_not_authorized": "ยังไม่ได้ให้สิทธิ์โพสต์ — เชื่อมบัญชี TikTok ใหม่",
            "privacy_level_option_mismatch": "ระดับการมองเห็นที่เลือกไม่อยู่ในตัวเลือกของบัญชีนี้",
        }.get(code, "")
        raise PublishError(f"{what}: {code} {msg} {hint}".strip())
    return data.get("data") or {}


# ---------------------------------------------------------------- accounts (OAuth)

class Accounts:
    def __init__(self, root: Path, session=None, sleep=time.sleep):
        self.root = Path(root)
        self.vault = Vault(root)
        self.http = session or requests.Session()
        self.sleep = sleep
        cfg = Path(os.environ.get("SHORTCLIP_PUBLISH_CONFIG") or self.root / "config/publish.json")
        self.cfg = json.loads(cfg.read_text()) if cfg.exists() else {}
        self.public_base = self.cfg.get("public_base", "http://localhost:8099").rstrip("/")
        self.ig_version = self.cfg.get("instagram_graph_version", "v27.0")

    def redirect_uri(self, platform: str) -> str:
        return GOOGLE_REDIRECT if platform == "youtube" else f"{self.public_base}/oauth/{platform}/callback"

    # ---- app credentials
    def google_client(self) -> dict:
        app = self.vault.load("youtube").get("app") or {}
        if app.get("client_id") and app.get("client_secret"):  # own Cloud project, pasted in the dashboard
            return {"client_id": app["client_id"], "client_secret": app["client_secret"], "file": None}
        path = app.get("client_secret_file")
        need(path and Path(path).exists(), "ยังไม่มี Google OAuth client (client_secret.json)")
        data = json.loads(Path(path).read_text())
        c = data.get("installed") or data.get("web") or {}
        need(c.get("client_id") and c.get("client_secret"), "client_secret.json ไม่มี client_id/secret")
        return {"client_id": c["client_id"], "client_secret": c["client_secret"], "file": path}

    def set_app(self, platform: str, data: dict) -> dict:
        need(platform in PLATFORMS, f"unknown platform {platform}")
        store = self.vault.load(platform)
        if platform == "youtube":
            cid, sec = str(data.get("client_id") or "").strip(), str(data.get("client_secret") or "").strip()
            path = str(data.get("client_secret_file") or "").strip()
            if cid or sec:
                need(cid.endswith(".apps.googleusercontent.com") and sec, "Client ID ต้องลงท้าย .apps.googleusercontent.com และต้องมี Client secret")
                store["app"] = {"client_id": cid, "client_secret": sec}
            else:
                need(not path or Path(path).exists(), "ไม่พบไฟล์ client_secret")
                store["app"] = {"client_secret_file": path} if path else {}
        elif platform == "tiktok":
            key, sec = str(data.get("client_key") or "").strip(), str(data.get("client_secret") or "").strip()
            need(key and sec, "ต้องมี Client key และ Client secret")
            store["app"] = {"client_key": key, "client_secret": sec}
        else:
            aid, sec = str(data.get("app_id") or "").strip(), str(data.get("app_secret") or "").strip()
            need(aid.isdigit() and sec, "ต้องมี Instagram App ID (ตัวเลข) และ App Secret")
            store["app"] = {"app_id": aid, "app_secret": sec}
        if store.get("app_fingerprint") != _fp(store["app"]):
            store.pop("token", None)  # tokens belong to the old app
        store["app_fingerprint"] = _fp(store["app"])
        self.vault.save(platform, store)
        return self.status()[platform]

    def status(self) -> dict:
        out = {}
        for p in PLATFORMS:
            s = self.vault.load(p)
            tok, app = s.get("token") or {}, s.get("app") or {}
            if p == "youtube":
                try:
                    self.google_client()
                    app_ok = True
                except PublishError:
                    app_ok = False
            else:
                app_ok = bool(app)
            out[p] = {"app_configured": app_ok, "connected": bool(tok.get("refresh_token") or tok.get("access_token")),
                      "account": s.get("account") or {}, "connected_at": tok.get("obtained_at"),
                      "refresh_expires_at": tok.get("refresh_expires_at"), "expires_at": tok.get("expires_at"),
                      "redirect_uri": self.redirect_uri(p)}
        return out

    def disconnect(self, platform: str) -> dict:
        s = self.vault.load(platform)
        s.pop("token", None)
        s.pop("account", None)
        self.vault.save(platform, s)
        return self.status()[platform]

    # ---- authorization start
    def _new_state(self, platform: str, extra: dict) -> str:
        states = {k: v for k, v in self.vault.load("oauth_state").items() if v.get("exp", 0) > time.time()}
        state = secrets.token_urlsafe(24)
        states[state] = {"platform": platform, "exp": time.time() + 900, **extra}
        self.vault.save("oauth_state", states)
        return state

    def _take_state(self, platform: str, state: str) -> dict:
        states = self.vault.load("oauth_state")
        entry = states.pop(state or "", None)
        self.vault.save("oauth_state", states)
        need(entry and entry.get("platform") == platform and entry.get("exp", 0) > time.time(),
             "ลิงก์เชื่อมบัญชีหมดอายุหรือไม่ถูกต้อง — กดเชื่อมใหม่")
        return entry

    def connect(self, platform: str) -> dict:
        need(platform in PLATFORMS, f"unknown platform {platform}")
        if platform == "youtube":
            c = self.google_client()
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            state = self._new_state(platform, {"verifier": verifier})
            q = {"client_id": c["client_id"], "redirect_uri": GOOGLE_REDIRECT, "response_type": "code",
                 "scope": SCOPES["youtube"], "access_type": "offline", "prompt": "consent", "state": state,
                 "code_challenge": challenge, "code_challenge_method": "S256"}
            return {"auth_url": url("google_auth") + "?" + urllib.parse.urlencode(q), "paste_back": True}
        app = self.vault.load(platform).get("app")
        need(app, f"ใส่ข้อมูลแอป {platform} ก่อน")
        state = self._new_state(platform, {})
        if platform == "tiktok":
            q = {"client_key": app["client_key"], "scope": SCOPES["tiktok"], "response_type": "code",
                 "redirect_uri": self.redirect_uri("tiktok"), "state": state}
            return {"auth_url": url("tt_auth") + "?" + urllib.parse.urlencode(q), "paste_back": False}
        q = {"client_id": app["app_id"], "redirect_uri": self.redirect_uri("instagram"), "response_type": "code",
             "scope": SCOPES["instagram"], "state": state, "enable_fb_login": "false"}
        return {"auth_url": url("ig_auth") + "?" + urllib.parse.urlencode(q), "paste_back": False}

    # ---- authorization finish
    def callback(self, platform: str, *, state: str = "", code: str = "", pasted_url: str = "") -> dict:
        if pasted_url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted_url.strip()).query)
            state, code = (q.get("state") or [""])[0], (q.get("code") or [""])[0]
            if q.get("error"):
                raise PublishError(f"ผู้ใช้ไม่อนุญาต: {q['error'][0]}")
        code = code.split("#")[0]
        need(code, "ไม่มี code ในลิงก์ — คัดลอกลิงก์ทั้งบรรทัดจากแถบที่อยู่")
        entry = self._take_state(platform, state)
        now = time.time()
        store = self.vault.load(platform)
        if platform == "youtube":
            c = self.google_client()
            t = _json(self.http.post(url("google_token"), data={
                "client_id": c["client_id"], "client_secret": c["client_secret"], "code": code,
                "code_verifier": entry["verifier"], "grant_type": "authorization_code",
                "redirect_uri": GOOGLE_REDIRECT}, timeout=30), "Google token")
            need(t.get("refresh_token"), "Google ไม่ให้ refresh token — ลองเชื่อมใหม่ (ต้องกดอนุญาตทุกช่อง)")
            store["token"] = {"access_token": t["access_token"], "refresh_token": t["refresh_token"],
                              "expires_at": now + int(t.get("expires_in", 3600)), "scope": t.get("scope"),
                              "obtained_at": now}
            self.vault.save(platform, store)
            ch = _json(self.http.get(url("yt_api") + "/channels", params={"part": "snippet", "mine": "true"},
                                     headers=self._auth("youtube"), timeout=30), "YouTube channel")
            items = ch.get("items") or []
            need(items, "บัญชี Google นี้ยังไม่มีช่อง YouTube — สร้างช่องก่อน")
            store["account"] = {"id": items[0]["id"], "name": items[0]["snippet"]["title"]}
        elif platform == "tiktok":
            app = store["app"]
            t = self.http.post(url("tt_api") + "/v2/oauth/token/", data={
                "client_key": app["client_key"], "client_secret": app["client_secret"], "code": code,
                "grant_type": "authorization_code", "redirect_uri": self.redirect_uri("tiktok")}, timeout=30)
            t = _json(t, "TikTok token")
            need(t.get("access_token"), f"TikTok token: {t.get('error_description') or t.get('error') or t}")
            store["token"] = _tt_token(t, now)
            self.vault.save(platform, store)
            info = self.http.get(url("tt_api") + "/v2/user/info/", params={"fields": "open_id,display_name,avatar_url"},
                                 headers=self._auth("tiktok"), timeout=30)
            user = (_tt(info, "TikTok user") or {}).get("user") or {}
            store["account"] = {"id": user.get("open_id") or t.get("open_id"), "name": user.get("display_name", "")}
        else:
            app = store["app"]
            t = _json(self.http.post(url("ig_token"), data={
                "client_id": app["app_id"], "client_secret": app["app_secret"], "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri("instagram"), "code": code}, timeout=30), "Instagram token")
            t = (t.get("data") or [t])[0]
            short, uid = t["access_token"], str(t.get("user_id"))
            need("instagram_business_content_publish" in str(t.get("permissions", "instagram_business_content_publish")),
                 "ไม่ได้ให้สิทธิ์โพสต์ (content_publish)")
            ll = _json(self.http.get(url("ig_graph") + "/access_token", params={
                "grant_type": "ig_exchange_token", "client_secret": app["app_secret"], "access_token": short},
                timeout=30), "Instagram long-lived token")
            store["token"] = {"access_token": ll["access_token"], "expires_at": now + int(ll.get("expires_in", 5184000)),
                              "obtained_at": now, "refreshed_at": now}
            self.vault.save(platform, store)
            me = _json(self.http.get(f"{url('ig_graph')}/{self.ig_version}/me",
                                     params={"fields": "user_id,username,account_type"},
                                     headers=self._auth("instagram"), timeout=30), "Instagram me")
            store["account"] = {"id": str(me.get("user_id") or me.get("id") or uid), "name": me.get("username", ""),
                                "type": me.get("account_type", "")}
        store["token"]["obtained_at"] = now
        self.vault.save(platform, store)
        return self.status()[platform]

    # ---- tokens
    def token(self, platform: str) -> str:
        store = self.vault.load(platform)
        tok = store.get("token") or {}
        need(tok, f"ยังไม่ได้เชื่อมบัญชี {platform}")
        now = time.time()
        if platform == "instagram":
            need(tok.get("expires_at", 0) > now, "token Instagram หมดอายุ (เกิน 60 วัน) — เชื่อมบัญชีใหม่")
            if now - tok.get("refreshed_at", now) > 86400:
                r = _json(self.http.get(url("ig_graph") + "/refresh_access_token", params={
                    "grant_type": "ig_refresh_token", "access_token": tok["access_token"]}, timeout=30), "IG refresh")
                tok.update(access_token=r["access_token"], expires_at=now + int(r.get("expires_in", 5184000)),
                           refreshed_at=now)
                store["token"] = tok
                self.vault.save(platform, store)
            return tok["access_token"]
        if tok.get("expires_at", 0) - 120 > now:
            return tok["access_token"]
        if platform == "youtube":
            c = self.google_client()
            resp = self.http.post(url("google_token"), data={
                "client_id": c["client_id"], "client_secret": c["client_secret"],
                "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
            if resp.status_code == 400 and "invalid_grant" in resp.text:
                # Testing-mode OAuth apps get refresh tokens that die after 7 days
                raise PublishError("สิทธิ์ YouTube หมดอายุ (แอป Google ยังอยู่โหมด Testing: ต้องเชื่อมใหม่ทุก 7 วัน) — "
                                   "ไปแท็บ บัญชีโพสต์ แล้วกดเชื่อมใหม่")
            r = _json(resp, "Google refresh")
            tok.update(access_token=r["access_token"], expires_at=now + int(r.get("expires_in", 3600)))
        else:
            need(tok.get("refresh_expires_at", 0) > now, "token TikTok หมดอายุ (เกิน 180 วัน) — เชื่อมบัญชีใหม่")
            app = store["app"]
            r = _json(self.http.post(url("tt_api") + "/v2/oauth/token/", data={
                "client_key": app["client_key"], "client_secret": app["client_secret"],
                "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}, timeout=30), "TikTok refresh")
            need(r.get("access_token"), f"TikTok refresh: {r.get('error_description') or r}")
            tok.update(_tt_token(r, now))  # refresh tokens rotate: keep the new one
        store["token"] = tok
        self.vault.save(platform, store)
        return tok["access_token"]

    def _auth(self, platform: str) -> dict:
        return {"Authorization": f"Bearer {self.token(platform)}"}

    def tiktok_creator(self) -> dict:
        r = self.http.post(url("tt_api") + "/v2/post/publish/creator_info/query/",
                           headers={**self._auth("tiktok"), "Content-Type": "application/json; charset=UTF-8"},
                           json={}, timeout=30)
        return _tt(r, "TikTok creator_info")


def _fp(app: dict) -> str:
    return hashlib.sha256(json.dumps(app, sort_keys=True).encode()).hexdigest()[:16]


def _tt_token(t: dict, now: float) -> dict:
    return {"access_token": t["access_token"], "refresh_token": t.get("refresh_token"), "open_id": t.get("open_id"),
            "scope": t.get("scope"), "expires_at": now + int(t.get("expires_in", 7200)),
            "refresh_expires_at": now + int(t.get("refresh_expires_in", 15552000)), "obtained_at": now}


# ---------------------------------------------------------------- post options

def clean_options(platform: str, opts: dict, creator: dict | None = None) -> dict:
    """Validate what the user chose on the post screen. Raises PublishError with a Thai message."""
    o = dict(opts or {})
    caption = str(o.get("caption") or "").strip()
    if platform == "youtube":
        title = str(o.get("title") or "").strip()
        need(title, "ใส่ชื่อคลิป YouTube")
        need(len(title) <= 100, "ชื่อ YouTube ยาวเกิน 100 ตัวอักษร")
        need("<" not in title and ">" not in title, "ชื่อ YouTube ห้ามมี < หรือ >")
        privacy = o.get("privacy") or "private"
        need(privacy in YT_PRIVACY, "ระดับการมองเห็น YouTube ไม่ถูกต้อง")
        tags = [str(t).strip().lstrip("#") for t in (o.get("tags") or []) if str(t).strip()][:30]
        need(sum(len(t) + 2 for t in tags) <= 500, "แท็ก YouTube รวมยาวเกิน 500 ตัวอักษร")
        need(len(caption) <= 5000, "คำอธิบาย YouTube ยาวเกิน 5000")
        return {"title": title, "description": caption, "tags": tags, "privacy": privacy,
                "made_for_kids": bool(o.get("made_for_kids")), "synthetic": bool(o.get("synthetic"))}
    if platform == "instagram":
        need(len(caption) <= 2200, "แคปชั่น IG ยาวเกิน 2200")
        need(caption.count("#") <= 30, "IG ใส่แฮชแท็กได้ไม่เกิน 30")
        return {"caption": caption, "share_to_feed": o.get("share_to_feed", True) is not False}
    need(platform == "tiktok", f"unknown platform {platform}")
    mode = o.get("mode") or "inbox"
    need(mode in ("inbox", "direct"), "โหมด TikTok ไม่ถูกต้อง")
    need(len(caption) <= 2200, "แคปชั่น TikTok ยาวเกิน 2200")
    if mode == "inbox":
        return {"mode": "inbox", "caption": caption}
    privacy = o.get("privacy") or ""
    need(privacy in TT_PRIVACY, "เลือกระดับการมองเห็นของ TikTok ก่อน (ไม่มีค่าเริ่มต้น)")
    if creator is not None:
        need(privacy in (creator.get("privacy_level_options") or []), "ระดับการมองเห็นนี้ใช้กับบัญชีนี้ไม่ได้")
    branded, own = bool(o.get("brand_content")), bool(o.get("brand_organic"))
    need(not (branded and privacy == "SELF_ONLY"), "Branded content ตั้งเป็น 'เฉพาะฉัน' ไม่ได้")
    need(o.get("music_ack") is True, "ต้องยอมรับ Music Usage Confirmation ของ TikTok ก่อนโพสต์")
    out = {"mode": "direct", "caption": caption, "privacy": privacy, "brand_content": branded, "brand_organic": own,
           "is_aigc": bool(o.get("is_aigc", True))}
    for k in ("allow_comment", "allow_duet", "allow_stitch"):
        out[k] = bool(o.get(k))
    if creator:
        if creator.get("comment_disabled"):
            out["allow_comment"] = False
        if creator.get("duet_disabled"):
            out["allow_duet"] = False
        if creator.get("stitch_disabled"):
            out["allow_stitch"] = False
    return out


def tiktok_chunks(size: int, chunk: int = 10 * MIB) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok's rules: <5 MB whole; chunks 5–64 MB; last absorbs the rest."""
    need(size > 0, "ไฟล์ว่าง")
    if size < 5 * MIB:
        return size, 1
    chunk = max(5 * MIB, min(64 * MIB, chunk))
    count = max(1, size // chunk)
    if size - chunk * (count - 1) > 128 * MIB:  # the last chunk must stay <=128 MB
        count = math.ceil(size / chunk)
    return chunk, min(count, 1000)


# ---------------------------------------------------------------- posting

class Poster:
    def __init__(self, root: Path, con, accounts: Accounts | None = None, sleep=time.sleep, poll_every=6.0,
                 poll_timeout=900):
        self.root = Path(root)
        self.con = con
        self.acc = accounts or Accounts(root, sleep=sleep)
        self.http = self.acc.http
        self.sleep = sleep
        self.poll_every = poll_every
        self.poll_timeout = poll_timeout

    # ---- rows
    def _set(self, post_id, **kw):
        cols = ", ".join(f"{k}=?" for k in kw)
        self.con.execute(f"UPDATE posts SET {cols}, updated_at=datetime('now') WHERE id=?", (*kw.values(), post_id))

    def get(self, post_id: int) -> dict:
        row = self.con.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        need(row is not None, f"post {post_id} not found")
        d = dict(row)
        for k in ("options_json", "response_json"):
            d[k[:-5]] = json.loads(d.pop(k)) if d.get(k) else None
        return d

    def list(self, episode_id: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM posts", ()
        if episode_id:
            sql, args = sql + " WHERE episode_id=?", (episode_id,)
        return [self.get(r["id"]) for r in self.con.execute(sql + " ORDER BY id DESC LIMIT 100", args)]

    def video_for(self, episode_id: int) -> Path:
        row = self.con.execute("""SELECT e.status, e.number, e.variant, s.slug,
                                         (SELECT params_json FROM jobs WHERE episode_id=e.id ORDER BY id DESC LIMIT 1) AS params,
                                         (SELECT status FROM jobs WHERE episode_id=e.id ORDER BY id DESC LIMIT 1) AS job_status
                                  FROM episodes e JOIN series s ON s.id=e.series_id WHERE e.id=?""", (episode_id,)).fetchone()
        need(row is not None, "ไม่พบตอนนี้")
        need(row["job_status"] in (None, "done") and row["status"] in ("published-ready", "qa_ok", "published"),
             "ตอนนี้ยังไม่ได้อนุมัติคลิปสุดท้าย — โพสต์ได้เฉพาะตอนที่ผ่าน gate2 แล้ว")
        epdir = Path(json.loads(row["params"] or "{}").get("epdir") or
                     self.root / "projects" / row["slug"] / f"ep{row['number']}{('v' + row['variant']) if row['variant'] else ''}")
        video = epdir / "renders" / "final.mp4"
        need(video.is_file(), f"ไม่พบ {video.relative_to(self.root) if self.root in video.parents else video}")
        return video

    def cover_for(self, episode_id: int) -> dict | None:
        """The cover chosen in the thumbnail step: {file (relative to root), frame_ms}."""
        from .thumbnail import selected
        video = self.video_for(episode_id)
        sel = selected(video.parent.parent)
        if not sel:
            return None
        path = video.parent.parent.resolve() / "thumbs" / sel["file"]
        return {"file": str(path.relative_to(self.root.resolve())), "frame_ms": int(sel.get("frame_ms") or 0)}

    def create(self, episode_id: int, platform: str, options: dict, *, again: bool = False) -> dict:
        need(platform in PLATFORMS, f"unknown platform {platform}")
        self.video_for(episode_id)
        creator = None
        if platform == "tiktok" and (options or {}).get("mode") == "direct":
            creator = self.acc.tiktok_creator()
        else:
            self.acc.token(platform)  # fail now if not connected
        opts = clean_options(platform, options, creator)
        cover = self.cover_for(episode_id) if (options or {}).get("use_cover", True) else None
        if cover:
            opts["cover"] = cover
        if creator:
            dur = self._duration(self.video_for(episode_id))
            need(not dur or dur <= int(creator.get("max_video_post_duration_sec") or 600),
                 "คลิปยาวเกินที่บัญชี TikTok นี้โพสต์ได้")
            opts["creator_nickname"] = creator.get("creator_nickname", "")
        busy = self.con.execute("""SELECT id,status FROM posts WHERE episode_id=? AND platform=?
                                   AND status IN ('queued','uploading','processing','inbox','published')""",
                                (episode_id, platform)).fetchall()
        need(again or not busy, f"ตอนนี้โพสต์/กำลังโพสต์ไป {platform} แล้ว (post #{busy[0]['id'] if busy else ''}) "
                                "ติ๊ก 'โพสต์ซ้ำ' ถ้าตั้งใจ")
        pid = self.con.execute("INSERT INTO posts(episode_id,platform,status,options_json) VALUES(?,?,'queued',?)",
                               (episode_id, platform, json.dumps(opts, ensure_ascii=False))).lastrowid
        return self.get(pid)

    @staticmethod
    def _duration(path: Path) -> float | None:
        try:
            import subprocess
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  str(path)], capture_output=True, text=True, timeout=30).stdout
            return float(out.strip())
        except (OSError, ValueError):
            return None

    def run(self, post_id: int) -> dict:
        post = self.get(post_id)
        need(post["status"] in ("queued", "failed"), f"post #{post_id} อยู่ในสถานะ {post['status']} แล้ว")
        video = self.video_for(post["episode_id"])
        self._set(post_id, status="uploading", error=None, attempts=(post["attempts"] or 0) + 1, pid=os.getpid())
        try:
            fn = {"youtube": self._youtube, "tiktok": self._tiktok, "instagram": self._instagram}[post["platform"]]
            fn(post_id, video, post["options"])
        except (PublishError, requests.RequestException, OSError, KeyError, ValueError) as exc:
            self._set(post_id, status="failed", error=str(exc)[-1500:], pid=None)
            raise PublishError(str(exc)) from exc
        self._set(post_id, pid=None)
        return self.get(post_id)

    # ---- YouTube
    def _youtube(self, post_id, video: Path, o: dict):
        size = video.stat().st_size
        meta = {"snippet": {"title": o["title"], "description": o["description"], "tags": o["tags"],
                            "categoryId": "1", "defaultLanguage": "th", "defaultAudioLanguage": "th"},
                "status": {"privacyStatus": o["privacy"], "selfDeclaredMadeForKids": o["made_for_kids"],
                           "containsSyntheticMedia": o["synthetic"], "embeddable": True}}
        r = self.http.post(url("yt_upload"), params={"uploadType": "resumable", "part": "snippet,status"},
                           headers={**self.acc._auth("youtube"), "Content-Type": "application/json; charset=UTF-8",
                                    "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
                           data=json.dumps(meta, ensure_ascii=False).encode(), timeout=60)
        if r.status_code >= 400:
            _json(r, "YouTube start upload")
        session = r.headers.get("Location")
        need(session, "YouTube ไม่ส่ง upload URL กลับมา")
        self._set(post_id, response_json=json.dumps({"session": "set"}))
        offset, chunk, tries = 0, 8 * MIB, 0
        with video.open("rb") as f:
            while True:
                f.seek(offset)
                data = f.read(chunk)
                end = offset + len(data) - 1
                try:
                    r = self.http.put(session, data=data, timeout=300, headers={
                        **self.acc._auth("youtube"), "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {offset}-{end}/{size}"})
                except requests.RequestException:
                    r = None
                if r is not None and r.status_code in (200, 201):
                    v = r.json()
                    vid = v["id"]
                    thumb = self._youtube_thumb(vid, o.get("cover"))
                    self._set(post_id, status="published" if o["privacy"] == "public" else "published_private",
                              remote_id=vid, url=f"https://youtube.com/shorts/{vid}",
                              response_json=json.dumps({"id": vid, "status": v.get("status"), "thumbnail": thumb},
                                                       ensure_ascii=False),
                              posted_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
                    return
                if r is not None and r.status_code == 308:
                    rng = r.headers.get("Range")  # "bytes=0-N" = what YouTube already has
                    offset = int(rng.split("-")[1]) + 1 if rng else 0
                    tries = 0
                    continue
                tries += 1
                need(tries <= 5 and (r is None or r.status_code >= 500),
                     f"YouTube upload: HTTP {getattr(r, 'status_code', 'network')} {getattr(r, 'text', '')[:300]}")
                self.sleep(min(60, 2 ** tries))
                q = self.http.put(session, timeout=60, headers={**self.acc._auth("youtube"), "Content-Length": "0",
                                                                "Content-Range": f"bytes */{size}"})
                if q.status_code == 308:
                    rng = q.headers.get("Range")
                    offset = int(rng.split("-")[1]) + 1 if rng else 0

    def _youtube_thumb(self, video_id: str, cover: dict | None) -> str:
        """Custom thumbnail. YouTube needs a phone-verified channel for this; a refusal is reported
        on the post, never fails it (the video is already up)."""
        if not cover:
            return "none"
        path = self.root / cover["file"]
        if not path.is_file():
            return "missing file"
        try:
            r = self.http.post(url("yt_thumb"), params={"videoId": video_id}, data=path.read_bytes(), timeout=120,
                               headers={**self.acc._auth("youtube"), "Content-Type": "image/jpeg"})
        except requests.RequestException as exc:
            return f"error: {exc}"[:200]
        if r.status_code < 300:
            return "set"
        try:
            reason = r.json()["error"]["errors"][0].get("reason") or r.json()["error"].get("message")
        except (ValueError, KeyError, IndexError, TypeError):
            reason = r.text[:120]
        return f"refused: {reason}"[:200]

    # ---- TikTok
    def _tiktok(self, post_id, video: Path, o: dict):
        size = video.stat().st_size
        chunk, count = tiktok_chunks(size)
        src = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        hdr = {**self.acc._auth("tiktok"), "Content-Type": "application/json; charset=UTF-8"}
        if o["mode"] == "inbox":
            init = _tt(self.http.post(url("tt_api") + "/v2/post/publish/inbox/video/init/", headers=hdr,
                                      json={"source_info": src}, timeout=60), "TikTok inbox init")
        else:
            creator = self.acc.tiktok_creator()  # must be fresh at post time
            need(o["privacy"] in (creator.get("privacy_level_options") or []), "ระดับการมองเห็นนี้ใช้กับบัญชีนี้ไม่ได้แล้ว")
            info = {"privacy_level": o["privacy"], "title": o["caption"],
                    "disable_comment": not o["allow_comment"] or bool(creator.get("comment_disabled")),
                    "disable_duet": not o["allow_duet"] or bool(creator.get("duet_disabled")),
                    "disable_stitch": not o["allow_stitch"] or bool(creator.get("stitch_disabled")),
                    "brand_content_toggle": o["brand_content"], "brand_organic_toggle": o["brand_organic"],
                    "is_aigc": o["is_aigc"]}
            if o.get("cover"):
                info["video_cover_timestamp_ms"] = int(o["cover"]["frame_ms"])
            init = _tt(self.http.post(url("tt_api") + "/v2/post/publish/video/init/", headers=hdr,
                                      json={"post_info": info, "source_info": src}, timeout=60), "TikTok direct init")
        publish_id, upload_url = init["publish_id"], init["upload_url"]
        self._set(post_id, remote_id=publish_id)
        with video.open("rb") as f:
            for i in range(count):
                start = i * chunk
                length = (size - start) if i == count - 1 else chunk
                f.seek(start)
                data = f.read(length)
                for attempt in range(4):
                    r = self.http.put(upload_url, data=data, timeout=300, headers={
                        "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{start + len(data) - 1}/{size}"})
                    if r.status_code in (200, 201, 206):
                        break
                    need(r.status_code >= 500 and attempt < 3, f"TikTok upload chunk {i + 1}/{count}: HTTP {r.status_code}")
                    self.sleep(2 ** attempt)
        self._set(post_id, status="processing")
        self._tiktok_poll(post_id, publish_id, o["mode"])

    def _tiktok_poll(self, post_id, publish_id, mode):
        deadline = time.time() + self.poll_timeout
        last = {}
        while time.time() < deadline:
            last = _tt(self.http.post(url("tt_api") + "/v2/post/publish/status/fetch/", json={"publish_id": publish_id},
                                      headers={**self.acc._auth("tiktok"), "Content-Type": "application/json; charset=UTF-8"},
                                      timeout=30), "TikTok status")
            st = last.get("status")
            if st == "FAILED":
                raise PublishError(f"TikTok: {last.get('fail_reason')}")
            if st == "SEND_TO_USER_INBOX" and mode == "inbox":
                self._set(post_id, status="inbox", response_json=json.dumps(last))
                return
            if st == "PUBLISH_COMPLETE":
                ids = last.get("publicaly_available_post_id") or []
                acct = (self.acc.vault.load("tiktok").get("account") or {}).get("name", "")
                self._set(post_id, status="published", response_json=json.dumps(last),
                          url=f"https://www.tiktok.com/@{acct}/video/{ids[0]}" if ids and acct else None,
                          posted_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
                return
            self.sleep(self.poll_every)
        self._set(post_id, status="processing", response_json=json.dumps(last),
                  error="ยังประมวลผลไม่เสร็จ — กด 'เช็กสถานะ' ทีหลัง")

    # ---- Instagram
    def _instagram(self, post_id, video: Path, o: dict):
        store = self.acc.vault.load("instagram")
        uid = (store.get("account") or {}).get("id")
        need(uid, "ยังไม่ได้เชื่อมบัญชี Instagram")
        rel = str(video.resolve().relative_to(self.root.resolve()))
        video_url = f"{self.acc.public_base}/pub/{sign_media(self.root, rel)}/video.mp4"
        g = f"{url('ig_graph')}/{self.acc.ig_version}"
        c = _json(self.http.post(f"{g}/{uid}/media", headers=self.acc._auth("instagram"), data={
            "media_type": "REELS", "video_url": video_url, "caption": o["caption"],
            "share_to_feed": "true" if o["share_to_feed"] else "false",
            **({"thumb_offset": str(int(o["cover"]["frame_ms"]))} if o.get("cover") else {})}, timeout=60), "IG container")
        container = c["id"]
        self._set(post_id, remote_id=container, status="processing")
        deadline = time.time() + self.poll_timeout
        status = {}
        while time.time() < deadline:
            status = _json(self.http.get(f"{g}/{container}", params={"fields": "status_code,status"},
                                         headers=self.acc._auth("instagram"), timeout=30), "IG status")
            code = status.get("status_code")
            if code == "FINISHED":
                break
            need(code not in ("ERROR", "EXPIRED"), f"Instagram ประมวลผลวิดีโอไม่ผ่าน: {status.get('status')}")
            self.sleep(self.poll_every)
        need(status.get("status_code") == "FINISHED", "Instagram ประมวลผลนานเกินไป — กด 'เช็กสถานะ' ทีหลัง")
        pub = _json(self.http.post(f"{g}/{uid}/media_publish", headers=self.acc._auth("instagram"),
                                   data={"creation_id": container}, timeout=60), "IG publish")
        media = pub["id"]
        link = _json(self.http.get(f"{g}/{media}", params={"fields": "permalink"},
                                   headers=self.acc._auth("instagram"), timeout=30), "IG permalink").get("permalink")
        self._set(post_id, status="published", remote_id=media, url=link, response_json=json.dumps(pub),
                  posted_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))

    # ---- later status check
    def refresh(self, post_id: int) -> dict:
        post = self.get(post_id)
        if post["platform"] == "tiktok" and post["remote_id"] and post["status"] in ("processing", "inbox"):
            self.poll_timeout, saved = 1, self.poll_timeout
            try:
                self._tiktok_poll(post_id, post["remote_id"], "direct" if post["status"] == "inbox" else post["options"]["mode"])
            finally:
                self.poll_timeout = saved
        elif post["platform"] == "youtube" and post["remote_id"]:
            v = _json(self.http.get(url("yt_api") + "/videos", params={"part": "status,processingDetails",
                                                                        "id": post["remote_id"]},
                                    headers=self.acc._auth("youtube"), timeout=30), "YouTube status")
            items = v.get("items") or []
            if not items:
                self._set(post_id, status="failed", error="วิดีโอถูกลบหรือไม่พบบน YouTube")
            else:
                st = items[0].get("status", {})
                self._set(post_id, response_json=json.dumps(items[0], ensure_ascii=False),
                          status="published" if st.get("privacyStatus") == "public" else "published_private")
        return self.get(post_id)


def default_texts(con, episode_id: int) -> dict:
    """Prefill for the post screen (the user edits before posting)."""
    row = con.execute("""SELECT e.title, e.number, e.script_json, s.slug, s.title AS series_title, s.rules_json
                         FROM episodes e JOIN series s ON s.id=e.series_id WHERE e.id=?""", (episode_id,)).fetchone()
    need(row is not None, "ไม่พบตอนนี้")
    script = json.loads(row["script_json"] or "{}")
    rules = json.loads(row["rules_json"] or "{}")
    cfg = Path(__file__).resolve().parent.parent / "config/publish.json"
    fallback = (json.loads(cfg.read_text()) if cfg.exists() else {}).get("default_hashtags") or ["#การ์ตูน"]
    tags = (rules.get("publish") or {}).get("hashtags") or fallback
    tags = [t if t.startswith("#") else "#" + t for t in tags]
    title = (row["title"] or f"{row['series_title']} EP.{row['number']}").strip()
    logline = (script.get("logline") or "").strip()
    cliff = "\nตอนต่อไปมาแน่ กดติดตามไว้ 👀"
    caption = f"{title}\n{logline}{cliff}\n\n{' '.join(tags)}".strip()
    return {"title": title[:100], "caption": caption, "hashtags": tags,
            "youtube_description": f"{caption} #Shorts", "youtube_tags": [t.lstrip("#") for t in tags] + ["Shorts"]}
