"""In-process fake of the YouTube / TikTok / Instagram endpoints used by shortclip.publish.

Mirrors the documented request/response shapes closely enough to exercise OAuth, token refresh,
resumable/chunked upload, status polling and error codes. Used by tests and tools/publish_selftest.py.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeState:
    def __init__(self):
        self.calls = []
        self.yt_bytes = bytearray()
        self.yt_size = 0
        self.yt_fail_once = True
        self.tt_chunks = []
        self.tt_size = 0
        self.tt_polls = 0
        self.tt_creator = {"creator_nickname": "ทดสอบ", "creator_username": "tester",
                           "privacy_level_options": ["FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                           "comment_disabled": False, "duet_disabled": True, "stitch_disabled": True,
                           "max_video_post_duration_sec": 600}
        self.tt_refresh = "rt-1"
        self.ig_polls = 0
        self.ig_fetched = None
        self.ig_fetch_status = None
        self.fetch_video = None  # callable(url) -> status, used to make IG really download our signed URL


def make_server(state: FakeState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body=None, headers=None):
            raw = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _form(self, raw):
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()}

        def _tt_ok(self, data):
            self._send(200, {"data": data, "error": {"code": "ok", "message": "", "log_id": "x"}})

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            state.calls.append(("GET", u.path, q))
            if u.path == "/yt_api/channels":
                return self._send(200, {"items": [{"id": "UC123", "snippet": {"title": "ช่องทดสอบ"}}]})
            if u.path == "/yt_api/videos":
                return self._send(200, {"items": [{"id": q["id"], "status": {"privacyStatus": "private",
                                                                             "uploadStatus": "processed"}}]})
            if u.path == "/tt_api/v2/user/info/":
                return self._tt_ok({"user": {"open_id": "open-1", "display_name": "tester"}})
            if u.path == "/ig_graph/access_token":
                return self._send(200, {"access_token": "IG-LONG", "token_type": "bearer", "expires_in": 5184000})
            if u.path == "/ig_graph/refresh_access_token":
                return self._send(200, {"access_token": "IG-LONG-2", "token_type": "bearer", "expires_in": 5184000})
            if u.path.endswith("/me"):
                return self._send(200, {"user_id": "1784", "username": "tester_ig", "account_type": "BUSINESS"})
            if u.path.endswith("/CONT1"):
                state.ig_polls += 1
                return self._send(200, {"status_code": "FINISHED" if state.ig_polls >= 2 else "IN_PROGRESS",
                                        "status": "ok"})
            if u.path.endswith("/MEDIA1"):
                return self._send(200, {"permalink": "https://www.instagram.com/reel/abc/"})
            self._send(404, {"error": "not found " + u.path})

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            raw = self._body()
            state.calls.append(("POST", u.path, raw[:2000]))
            if u.path == "/google_token":
                f = self._form(raw)
                if f.get("grant_type") == "authorization_code":
                    assert f.get("code_verifier"), "PKCE verifier missing"
                    return self._send(200, {"access_token": "ya-1", "refresh_token": "g-rt", "expires_in": 3599,
                                            "scope": "youtube.upload"})
                return self._send(200, {"access_token": "ya-2", "expires_in": 3599})
            if u.path == "/yt_thumb":
                state.thumbs = getattr(state, "thumbs", []) + [(urllib.parse.parse_qs(u.query).get("videoId", [""])[0],
                                                                 len(raw), self.headers.get("Content-Type"))]
                if getattr(state, "thumb_refuse", False):
                    return self._send(403, {"error": {"message": "forbidden", "errors": [{"reason": "forbidden"}]}})
                return self._send(200, {"items": [{"default": {"url": "x"}}]})
            if u.path == "/yt_upload":
                meta = json.loads(raw)
                assert meta["status"]["privacyStatus"] in ("private", "unlisted", "public")
                state.yt_size = int(self.headers["X-Upload-Content-Length"])
                port = self.server.server_address[1]
                return self._send(200, {}, {"Location": f"http://127.0.0.1:{port}/yt_session"})
            if u.path == "/tt_api/v2/oauth/token/":
                f = self._form(raw)
                if f["grant_type"] == "refresh_token":
                    if f["refresh_token"] != state.tt_refresh:
                        return self._send(400, {"error": "invalid_grant", "error_description": "stale refresh token"})
                    state.tt_refresh = "rt-2"
                return self._send(200, {"access_token": "act-1", "expires_in": 7200, "refresh_token": state.tt_refresh,
                                        "refresh_expires_in": 15552000, "open_id": "open-1",
                                        "scope": "video.upload,video.publish", "token_type": "Bearer"})
            if u.path == "/tt_api/v2/post/publish/creator_info/query/":
                return self._tt_ok(state.tt_creator)
            if u.path in ("/tt_api/v2/post/publish/inbox/video/init/", "/tt_api/v2/post/publish/video/init/"):
                body = json.loads(raw)
                src = body["source_info"]
                if "post_info" in body:
                    pi = body["post_info"]
                    if pi["privacy_level"] not in state.tt_creator["privacy_level_options"]:
                        return self._send(403, {"data": {}, "error": {"code": "privacy_level_option_mismatch",
                                                                     "message": "bad"}})
                state.tt_size = src["video_size"]
                state.tt_init = body
                port = self.server.server_address[1]
                return self._tt_ok({"publish_id": "v_pub~1", "upload_url": f"http://127.0.0.1:{port}/tt_upload?x=1"})
            if u.path == "/tt_api/v2/post/publish/status/fetch/":
                state.tt_polls += 1
                inbox = "inbox" in json.dumps(getattr(state, "tt_init", {})) or "post_info" not in getattr(state, "tt_init", {})
                if state.tt_polls < 2:
                    return self._tt_ok({"status": "PROCESSING_UPLOAD"})
                return self._tt_ok({"status": "SEND_TO_USER_INBOX" if inbox else "PUBLISH_COMPLETE",
                                    "publicaly_available_post_id": [] if inbox else [7123]})
            if u.path.endswith("/1784/media"):
                f = self._form(raw)
                assert f["media_type"] == "REELS"
                state.ig_fetched = f["video_url"]
                if state.fetch_video:
                    state.ig_fetch_status = state.fetch_video(f["video_url"])
                    if state.ig_fetch_status != 200:
                        return self._send(400, {"error": {"message": "Media download has failed"}})
                return self._send(200, {"id": "CONT1"})
            if u.path.endswith("/1784/media_publish"):
                return self._send(200, {"id": "MEDIA1"})
            if u.path == "/ig_token":
                return self._send(200, {"data": [{"access_token": "IG-SHORT", "user_id": "1784",
                                                  "permissions": "instagram_business_basic,instagram_business_content_publish"}]})
            self._send(404, {"error": "not found " + u.path})

        def do_PUT(self):
            u = urllib.parse.urlparse(self.path)
            raw = self._body()
            rng = self.headers.get("Content-Range", "")
            state.calls.append(("PUT", u.path, rng))
            if u.path == "/yt_session":
                if rng.startswith("bytes */"):
                    h = {"Range": f"bytes=0-{len(state.yt_bytes) - 1}"} if state.yt_bytes else {}
                    return self._send(308, None, h)
                start = int(rng.split(" ")[1].split("-")[0])
                if state.yt_fail_once and state.yt_bytes:
                    state.yt_fail_once = False  # connection dropped mid-chunk: half of it was stored
                    state.yt_bytes += raw[: len(raw) // 2]
                    return self._send(503, {"error": "backend"})
                assert start == len(state.yt_bytes), (start, len(state.yt_bytes))
                state.yt_bytes += raw
                if len(state.yt_bytes) >= state.yt_size:
                    return self._send(200, {"id": "VID123", "status": {"privacyStatus": "private"}})
                return self._send(308, None, {"Range": f"bytes=0-{len(state.yt_bytes) - 1}"})
            if u.path == "/tt_upload":
                assert self.headers["Content-Type"] == "video/mp4"
                assert int(self.headers["Content-Length"]) == len(raw)
                state.tt_chunks.append((rng, len(raw)))
                done = sum(n for _, n in state.tt_chunks)
                return self._send(201 if done >= state.tt_size else 206, {})
            self._send(404, {})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
