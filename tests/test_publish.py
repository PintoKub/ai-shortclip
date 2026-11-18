"""Publishing: OAuth, token refresh, upload protocols and post-screen rules, against tests/fake_platforms.py."""
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fake_platforms import FakeState, make_server  # noqa: E402
from shortclip import db as dbm  # noqa: E402
from shortclip import publish  # noqa: E402
from shortclip.publish import Accounts, Poster, PublishError, clean_options, tiktok_chunks  # noqa: E402

MIB = 1024 * 1024


class PublishBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("db", "config"):
            (self.root / d).mkdir()
        shutil.copy2(ROOT / "db/schema.sql", self.root / "db/schema.sql")
        (self.root / "config/publish.json").write_text(json.dumps({"public_base": "https://example.test"}))
        secret = self.root / "client_secret.json"
        secret.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}))
        self.secret_file = secret
        self.con = dbm.connect(self.root / "db/t.db")
        dbm.init_db(self.con)
        sid = self.con.execute("INSERT INTO series(slug,title) VALUES('s','ซีรีส์')").lastrowid
        self.ep = self.con.execute("""INSERT INTO episodes(series_id,number,title,status,script_json)
                                      VALUES(?,1,'ซีรีส์ EP.1 : ทดสอบ','published-ready',?)""",
                                   (sid, json.dumps({"logline": "เรื่องย่อ"}))).lastrowid
        epdir = self.root / "projects/s/ep1"
        (epdir / "renders").mkdir(parents=True)
        self.video = epdir / "renders/final.mp4"
        self.video.write_bytes(os.urandom(int(12.5 * MIB)))
        self.con.execute("INSERT INTO jobs(episode_id,kind,status,params_json) VALUES(?,'new_episode','done',?)",
                         (self.ep, json.dumps({"epdir": str(epdir)})))
        self.state = FakeState()
        self.srv = make_server(self.state)
        os.environ["SHORTCLIP_PUBLISH_FAKE"] = f"http://127.0.0.1:{self.srv.server_address[1]}"
        os.environ.pop("SHORTCLIP_SECRETS", None)
        os.environ.pop("SHORTCLIP_PUBLISH_CONFIG", None)
        self.acc = Accounts(self.root, sleep=lambda s: None)
        self.acc.set_app("youtube", {"client_secret_file": str(secret)})
        self.poster = Poster(self.root, self.con, self.acc, sleep=lambda s: None, poll_every=0)
        Poster._duration = staticmethod(lambda p: 55.0)

    def tearDown(self):
        os.environ.pop("SHORTCLIP_PUBLISH_FAKE", None)
        self.srv.shutdown()
        self.con.close()
        self.tmp.cleanup()

    def connect(self, platform):
        start = self.acc.connect(platform)
        q = dict(x.split("=", 1) for x in start["auth_url"].split("?", 1)[1].split("&"))
        if platform == "youtube":
            return self.acc.callback("youtube", pasted_url=f"http://localhost:1/?state={q['state']}&code=4%2Fabc&scope=x")
        return self.acc.callback(platform, state=q["state"], code="CODE#_")


class AccountTests(PublishBase):
    def test_youtube_paste_back_with_pkce_and_refresh(self):
        st = self.connect("youtube")
        self.assertTrue(st["connected"])
        self.assertEqual(st["account"]["name"], "ช่องทดสอบ")
        self.assertIn("code_challenge=", self.acc.connect("youtube")["auth_url"])
        tok = self.acc.vault.load("youtube")["token"]
        tok["expires_at"] = 0
        store = self.acc.vault.load("youtube")
        store["token"] = tok
        self.acc.vault.save("youtube", store)
        self.assertEqual(self.acc.token("youtube"), "ya-2")

    def test_youtube_own_project_client(self):
        with self.assertRaises(PublishError):
            self.acc.set_app("youtube", {"client_id": "abc", "client_secret": "x"})
        self.acc.set_app("youtube", {"client_id": "123-x.apps.googleusercontent.com", "client_secret": "own"})
        self.assertIn("client_id=123-x.apps.googleusercontent.com", self.acc.connect("youtube")["auth_url"])
        self.assertTrue(self.connect("youtube")["connected"])

    def test_state_is_single_use_and_checked(self):
        start = self.acc.connect("youtube")
        state = start["auth_url"].split("state=")[1].split("&")[0]
        with self.assertRaises(PublishError):
            self.acc.callback("youtube", pasted_url="http://localhost:1/?state=forged&code=x")
        self.acc.callback("youtube", pasted_url=f"http://localhost:1/?state={state}&code=x")
        with self.assertRaises(PublishError):
            self.acc.callback("youtube", pasted_url=f"http://localhost:1/?state={state}&code=x")

    def test_tiktok_refresh_token_rotates(self):
        self.acc.set_app("tiktok", {"client_key": "ck", "client_secret": "cs"})
        self.connect("tiktok")
        store = self.acc.vault.load("tiktok")
        store["token"]["expires_at"] = 0
        self.acc.vault.save("tiktok", store)
        self.acc.token("tiktok")
        self.assertEqual(self.acc.vault.load("tiktok")["token"]["refresh_token"], "rt-2")
        store = self.acc.vault.load("tiktok")
        store["token"]["expires_at"] = 0
        self.acc.vault.save("tiktok", store)
        self.acc.token("tiktok")  # must use rt-2; the fake rejects a stale token

    def test_secrets_are_private_and_new_app_drops_old_token(self):
        self.acc.set_app("instagram", {"app_id": "123", "app_secret": "s"})
        self.connect("instagram")
        d = self.root / "secrets/publish"
        if os.name != "nt":  # Windows has no POSIX modes (chmod only toggles read-only); privacy there = the user profile ACL
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
            for f in d.glob("*.json"):
                self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600, f.name)
        self.assertNotIn("IG-LONG", json.dumps(self.acc.status()))
        self.acc.set_app("instagram", {"app_id": "999", "app_secret": "s2"})
        self.assertFalse(self.acc.status()["instagram"]["connected"])

    def test_signed_media_url(self):
        rel = "projects/s/ep1/renders/final.mp4"
        tok = publish.sign_media(self.root, rel, ttl=60)
        self.assertEqual(publish.check_media(self.root, tok), self.video.resolve())
        self.assertIsNone(publish.check_media(self.root, tok, now=time.time() + 120))
        self.assertIsNone(publish.check_media(self.root, tok[:-2] + "00"))
        evil = publish.sign_media(self.root, "secrets/publish/youtube.json")
        self.assertIsNone(publish.check_media(self.root, evil))


class OptionTests(unittest.TestCase):
    def test_tiktok_direct_post_rules(self):
        creator = {"privacy_level_options": ["SELF_ONLY", "FOLLOWER_OF_CREATOR"], "duet_disabled": True}
        base = {"mode": "direct", "caption": "x", "music_ack": True, "allow_duet": True}
        with self.assertRaisesRegex(PublishError, "ไม่มีค่าเริ่มต้น"):
            clean_options("tiktok", base, creator)
        with self.assertRaises(PublishError):
            clean_options("tiktok", {**base, "privacy": "PUBLIC_TO_EVERYONE"}, creator)
        with self.assertRaisesRegex(PublishError, "Branded"):
            clean_options("tiktok", {**base, "privacy": "SELF_ONLY", "brand_content": True}, creator)
        with self.assertRaisesRegex(PublishError, "Music"):
            clean_options("tiktok", {**base, "privacy": "SELF_ONLY", "music_ack": False}, creator)
        o = clean_options("tiktok", {**base, "privacy": "SELF_ONLY"}, creator)
        self.assertFalse(o["allow_duet"], "creator disabled duet: must be forced off")
        self.assertFalse(o["allow_comment"], "interaction toggles default off")
        self.assertEqual(clean_options("tiktok", {"caption": "x"})["mode"], "inbox")

    def test_youtube_and_instagram_limits(self):
        self.assertEqual(clean_options("youtube", {"title": "t"})["privacy"], "private")
        for bad in ({"title": ""}, {"title": "a" * 101}, {"title": "a<b"}, {"title": "t", "privacy": "x"}):
            with self.assertRaises(PublishError):
                clean_options("youtube", bad)
        with self.assertRaises(PublishError):
            clean_options("instagram", {"caption": " ".join(f"#t{i}" for i in range(31))})

    def test_tiktok_chunk_math(self):
        self.assertEqual(tiktok_chunks(4 * MIB), (4 * MIB, 1))
        self.assertEqual(tiktok_chunks(int(12.5 * MIB)), (10 * MIB, 1))
        self.assertEqual(tiktok_chunks(50_000_123, 10_000_000), (10_000_000, 5))


class PostTests(PublishBase):
    def test_youtube_resumable_upload_survives_a_503(self):
        self.connect("youtube")
        post = self.poster.create(self.ep, "youtube", {"title": "ทดสอบ", "caption": "d", "tags": ["a"]})
        done = self.poster.run(post["id"])
        self.assertEqual(done["status"], "published_private")
        self.assertEqual(done["url"], "https://youtube.com/shorts/VID123")
        self.assertEqual(bytes(self.state.yt_bytes), self.video.read_bytes())
        self.assertFalse(self.state.yt_fail_once, "the fake 503 was exercised")

    def test_tiktok_inbox_and_direct(self):
        self.acc.set_app("tiktok", {"client_key": "ck", "client_secret": "cs"})
        self.connect("tiktok")
        p = self.poster.run(self.poster.create(self.ep, "tiktok", {"mode": "inbox", "caption": "c"})["id"])
        self.assertEqual(p["status"], "inbox")
        size = self.video.stat().st_size
        self.assertEqual(sum(n for _, n in self.state.tt_chunks), size)
        self.assertEqual(self.state.tt_chunks[-1][0], f"bytes 0-{size - 1}/{size}")
        self.state.tt_chunks, self.state.tt_polls = [], 0
        with self.assertRaises(PublishError):  # already sent to inbox
            self.poster.create(self.ep, "tiktok", {"mode": "inbox"})
        p = self.poster.create(self.ep, "tiktok", {"mode": "direct", "caption": "c", "privacy": "SELF_ONLY",
                                                   "music_ack": True, "allow_duet": True}, again=True)
        self.assertEqual(p["options"]["creator_nickname"], "ทดสอบ")
        p = self.poster.run(p["id"])
        self.assertEqual(p["status"], "published")
        info = self.state.tt_init["post_info"]
        self.assertTrue(info["disable_duet"])
        self.assertTrue(info["is_aigc"])

    def test_instagram_reel_fetches_signed_url(self):
        self.acc.set_app("instagram", {"app_id": "123", "app_secret": "s"})
        self.connect("instagram")
        seen = {}

        def fetch(u):
            tok = u.split("/pub/")[1].split("/")[0]
            seen["path"] = publish.check_media(self.root, tok)
            return 200 if seen["path"] else 403
        self.state.fetch_video = fetch
        p = self.poster.run(self.poster.create(self.ep, "instagram", {"caption": "#a #b"})["id"])
        self.assertEqual(p["status"], "published")
        self.assertEqual(p["url"], "https://www.instagram.com/reel/abc/")
        self.assertEqual(seen["path"], self.video.resolve())
        self.assertTrue(self.state.ig_fetched.startswith("https://example.test/pub/"))

    def test_only_approved_episodes_and_failures_are_recorded(self):
        self.connect("youtube")
        self.con.execute("UPDATE jobs SET status='waiting_gate'")
        with self.assertRaisesRegex(PublishError, "gate2"):
            self.poster.create(self.ep, "youtube", {"title": "t"})
        self.con.execute("UPDATE jobs SET status='done'")
        post = self.poster.create(self.ep, "youtube", {"title": "t"})
        self.video.unlink()
        with self.assertRaises(PublishError):
            self.poster.run(post["id"])
        self.assertEqual(self.poster.get(post["id"])["status"], "queued")  # nothing was sent

    def _choose_cover(self, frame_ms=12300):
        thumbs = self.video.parent.parent / "thumbs"
        thumbs.mkdir()
        (thumbs / "cover-1.jpg").write_bytes(b"\xff\xd8JPEGDATA")
        (thumbs / "selected.json").write_text(json.dumps({"file": "cover-1.jpg", "frame_ms": frame_ms}))

    def test_cover_goes_to_every_platform(self):
        self._choose_cover()
        self.connect("youtube")
        p = self.poster.run(self.poster.create(self.ep, "youtube", {"title": "t"})["id"])
        self.assertEqual(self.state.thumbs, [("VID123", 10, "image/jpeg")])
        self.assertIn('"thumbnail": "set"', p["response_json"] if "response_json" in p else
                      self.con.execute("SELECT response_json FROM posts WHERE id=?", (p["id"],)).fetchone()[0])
        self.acc.set_app("tiktok", {"client_key": "ck", "client_secret": "cs"})
        self.connect("tiktok")
        self.poster.run(self.poster.create(self.ep, "tiktok", {"mode": "direct", "caption": "c", "privacy": "SELF_ONLY",
                                                               "music_ack": True})["id"])
        self.assertEqual(self.state.tt_init["post_info"]["video_cover_timestamp_ms"], 12300)
        self.acc.set_app("instagram", {"app_id": "123", "app_secret": "s"})
        self.connect("instagram")
        self.state.fetch_video = lambda u: 200
        self.poster.run(self.poster.create(self.ep, "instagram", {"caption": "x"})["id"])
        ig = [c for c in self.state.calls if c[1].endswith("/media") and c[0] == "POST"][-1]
        self.assertIn(b"thumb_offset=12300", ig[2])

    def test_refused_youtube_thumbnail_does_not_fail_the_post(self):
        self._choose_cover()
        self.state.thumb_refuse = True
        self.connect("youtube")
        p = self.poster.run(self.poster.create(self.ep, "youtube", {"title": "t"})["id"])
        self.assertEqual(p["status"], "published_private")
        row = self.con.execute("SELECT response_json FROM posts WHERE id=?", (p["id"],)).fetchone()[0]
        self.assertIn("refused: forbidden", row)

    def test_no_cover_chosen_sends_nothing_extra(self):
        self.connect("youtube")
        self.poster.run(self.poster.create(self.ep, "youtube", {"title": "t"})["id"])
        self.assertFalse(getattr(self.state, "thumbs", []))

    def test_not_connected_fails_before_creating_a_row(self):
        with self.assertRaisesRegex(PublishError, "ยังไม่ได้เชื่อม"):
            self.poster.create(self.ep, "instagram", {"caption": "x"})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
