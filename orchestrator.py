#!/usr/bin/env python3
"""AI ShortClip Phase-1 orchestrator (Python/subprocess only; no LLM SDK).

Examples:
  python3 orchestrator.py new --series zombie-school --ep 3
  python3 orchestrator.py status --job 3 --json
  python3 orchestrator.py advance 3 --approve
  python3 orchestrator.py advance 3 --reject "hook ยังไม่ชัด"
  python3 orchestrator.py resume 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shortclip.compat import ensure_utf8_mode
from shortclip.external import StepError
from shortclip.pipeline import Pipeline
from shortclip.validate import ValidationError

ROOT = Path(__file__).resolve().parent


def emit(data, as_json=False):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return
    if "job" in data:
        j, e = data["job"], data["episode"]
        print(f"job #{j['id']}  {e['title']}  [{j['status']}]" + (f" gate={j['gate']}" if j['gate'] else ""))
        if j.get("error"):
            print("error:", j["error"])
        for s in data["steps"]:
            cost = f" ${s['cost_usd']:.4f}" if s.get("cost_usd") else ""
            mark = {"done": "✓", "running": "▶", "failed": "✗"}.get(s["status"], "·")
            print(f" {mark} {s['position']}. {s['role']:9s} {s['status']:9s} attempt={s['attempt']}{cost}")
            if s["role"] == "writer" and s.get("output_json"):
                out = json.loads(s["output_json"])
                if out.get("title_options"):
                    print("   ชื่อที่เสนอ:")
                    for title in out["title_options"]:
                        print("    -", title)
        if j["status"] == "waiting_gate":
            print(f"\nรออนุมัติ {j['gate']}: python3 orchestrator.py advance {j['id']} --approve")
            print(f"หรือสั่งแก้: python3 orchestrator.py advance {j['id']} --reject \"เหตุผล\"")
    elif "episodes" in data:
        for e in data["episodes"]:
            print(f"EP.{e['number']}{('v' + e['variant']) if e['variant'] else ''}  {e['status']:15s} "
                  f"{e['duration'] or '-'}s  ${e['cost_usd']:.4f}  {e['title']}")
        print(f"total notional: ${data['total_cost_usd']:.4f}")
        if data["queue"]:
            print("queue:", data["queue"])
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=None, help="override SQLite path (or SHORTCLIP_DB)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="create a job and stop at gate1 after the script")
    new.add_argument("--series", required=True)
    new.add_argument("--ep", type=int, required=True)
    new.add_argument("--variant", default="")
    new.add_argument("--hook", choices=["countdown", "forbidden", "midscene", "lie"], default="countdown")
    new.add_argument("--idea", default="")
    new.add_argument("--fixture", help="test only: use an existing script instead of calling Claude")
    new.add_argument("--no-run", action="store_true")
    new.add_argument("--requested-by", default="cli")
    new.add_argument("--voice-json", help='{"engine":"wayu","voice":"f_young_warm","speed":1.25}')
    new.add_argument("--rules-json", help='{"story":["..."],"visual":["..."]} for this episode only')
    new.add_argument("--target-seconds", type=int)
    new.add_argument("--models-json", help='{"writer": "...", "animator": "..."} for this episode')

    voice = sub.add_parser("voice", help="set an episode's narrator; past gate1 it re-voices and re-renders")
    voice.add_argument("job_id", type=int)
    voice.add_argument("--engine", required=True, choices=["edge", "wayu"])
    voice.add_argument("--voice", required=True)
    voice.add_argument("--speed", type=float, default=1.0)

    plan = sub.add_parser("plan", help="story planning: idea → concepts → choose → summary → bible → series")
    psub = plan.add_subparsers(dest="plan_cmd", required=True)
    pn = psub.add_parser("new"); pn.add_argument("--idea", required=True); pn.add_argument("--options-json")
    pn.add_argument("--run", action="store_true", help="also generate concepts now")
    pc = psub.add_parser("concepts"); pc.add_argument("plan_id", type=int); pc.add_argument("--extra", default="")
    pch = psub.add_parser("choose"); pch.add_argument("plan_id", type=int); pch.add_argument("--concept-json", required=True)
    pch.add_argument("--notes", default="")
    pss = psub.add_parser("save-summary"); pss.add_argument("plan_id", type=int); pss.add_argument("--summary-json", required=True)
    pb = psub.add_parser("bible"); pb.add_argument("plan_id", type=int); pb.add_argument("--opts-json", default="{}")
    psb = psub.add_parser("save-bible"); psb.add_argument("plan_id", type=int)
    psb.add_argument("--bible-json", help="bible JSON (or '-' / omitted = read stdin; large)"); psb.add_argument("--expect-updated")
    prg = psub.add_parser("regen"); prg.add_argument("plan_id", type=int)
    prg.add_argument("target", help="character:<id|new> episode:<n|new> lore:<id|new> world rules")
    prg.add_argument("--instruction", default=""); prg.add_argument("--model")
    psr = psub.add_parser("sources"); psr.add_argument("plan_id", type=int)
    pls = psub.add_parser("lore-search"); pls.add_argument("plan_id", type=int); pls.add_argument("query")
    pls.add_argument("--lang", default="th,en")
    pa = psub.add_parser("approve"); pa.add_argument("plan_id", type=int); pa.add_argument("--slug")
    pg = psub.add_parser("show"); pg.add_argument("plan_id", type=int)
    psub.add_parser("list")

    pub = sub.add_parser("publish", help="post finished episodes to YouTube / TikTok / Instagram")
    pubs = pub.add_subparsers(dest="pub_cmd", required=True)
    pubs.add_parser("accounts")
    pa_ = pubs.add_parser("set-app", help="app credentials as JSON on stdin (never on the command line)")
    pa_.add_argument("platform", choices=["youtube", "tiktok", "instagram"])
    pc_ = pubs.add_parser("connect"); pc_.add_argument("platform", choices=["youtube", "tiktok", "instagram"])
    pf_ = pubs.add_parser("finish", help="finish OAuth: --pasted-url (YouTube) or --state/--code (callback)")
    pf_.add_argument("platform", choices=["youtube", "tiktok", "instagram"])
    pf_.add_argument("--pasted-url", default=""); pf_.add_argument("--state", default=""); pf_.add_argument("--code", default="")
    pd_ = pubs.add_parser("disconnect"); pd_.add_argument("platform", choices=["youtube", "tiktok", "instagram"])
    pubs.add_parser("creator", help="TikTok creator_info for the post screen")
    pn_ = pubs.add_parser("new"); pn_.add_argument("episode_id", type=int)
    pn_.add_argument("platform", choices=["youtube", "tiktok", "instagram"]); pn_.add_argument("--options-json", required=True)
    pn_.add_argument("--again", action="store_true"); pn_.add_argument("--run", action="store_true")
    pr_ = pubs.add_parser("run"); pr_.add_argument("post_id", type=int)
    ps_ = pubs.add_parser("refresh"); ps_.add_argument("post_id", type=int)
    pl_ = pubs.add_parser("list"); pl_.add_argument("--episode", type=int)
    pt_ = pubs.add_parser("defaults"); pt_.add_argument("episode_id", type=int)

    mod = sub.add_parser("models", help="show or set which Claude model each role uses")
    mod.add_argument("--set", action="append", default=[], metavar="ROLE=MODEL")
    pm = psub.add_parser("model"); pm.add_argument("plan_id", type=int); pm.add_argument("model")

    series = sub.add_parser("series", help="show or update series rules / default voice")
    series.add_argument("slug")
    series.add_argument("--rules-json")
    series.add_argument("--voice-json")
    series.add_argument("--title")
    series.add_argument("--genre")

    status = sub.add_parser("status")
    status.add_argument("--job", type=int)
    status.add_argument("--json", action="store_true", dest="sub_json")

    advance = sub.add_parser("advance")
    advance.add_argument("job_id", type=int)
    group = advance.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", metavar="REASON")

    resume = sub.add_parser("resume")
    resume.add_argument("job_id", type=int)

    cancel = sub.add_parser("cancel", help="stop a job for good (keeps files and cost history)")
    cancel.add_argument("job_id", type=int)

    th = sub.add_parser("thumb", help="clickbait cover: frames → AI ideas → render → select")
    ths = th.add_subparsers(dest="thumb_cmd", required=True)
    for name in ("state", "frames", "ideas", "render", "select", "delete"):
        x = ths.add_parser(name)
        x.add_argument("episode_id", type=int)
        if name == "ideas":
            x.add_argument("--count", type=int, default=5)
            x.add_argument("--extra", default="")
            x.add_argument("--model")
        if name == "frames":
            x.add_argument("--force", action="store_true")
        if name == "render":
            x.add_argument("--spec-json", required=True)
        if name in ("select", "delete"):
            x.add_argument("file")

    redo = sub.add_parser("redo", help="do a stage again (also reopens a cancelled/failed/finished job)")
    redo.add_argument("job_id", type=int)
    redo.add_argument("--from", dest="from_role", required=True, choices=["writer", "sound", "animator"])
    redo.add_argument("--reason", default="", help="what to change (writer/animator)")
    redo.add_argument("--fresh", action="store_true", help="animator: draw from scratch instead of fixing")
    redo.add_argument("--beats", help="sound: comma list of beats to re-voice; empty string = keep takes, re-time only")
    redo.add_argument("--no-run", action="store_true")

    es = sub.add_parser("edit-script", help="save a hand-edited script (JSON on stdin) and redo what depends on it")
    es.add_argument("job_id", type=int)
    es.add_argument("--no-run", action="store_true")

    ls = sub.add_parser("list")
    ls.add_argument("--limit", type=int, default=20)

    args = p.parse_args(argv)
    pipe = Pipeline(ROOT, args.db)
    try:
        if args.command == "new":
            result = pipe.create(args.series, args.ep, variant=args.variant, hook=args.hook,
                                 idea=args.idea, fixture=args.fixture, requested_by=args.requested_by,
                                 run=not args.no_run,
                                 voice=json.loads(args.voice_json) if args.voice_json else None,
                                 rules=json.loads(args.rules_json) if args.rules_json else None,
                                 target_seconds=args.target_seconds,
                                 models=json.loads(args.models_json) if args.models_json else None)
        elif args.command == "plan":
            from shortclip.planning import Planner
            pl = Planner(ROOT, pipe.con)
            c = args.plan_cmd
            if c == "new":
                pid = pl.create(args.idea, json.loads(args.options_json) if args.options_json else None)
                result = pl.concepts(pid) if args.run else pl.get(pid)
            elif c == "concepts":
                result = pl.concepts(args.plan_id, args.extra)
            elif c == "choose":
                result = pl.choose(args.plan_id, json.loads(args.concept_json), args.notes)
            elif c == "save-summary":
                result = pl.save_summary(args.plan_id, json.loads(args.summary_json))
            elif c == "bible":
                result = pl.bible(args.plan_id, json.loads(args.opts_json))
            elif c == "save-bible":
                raw = args.bible_json if args.bible_json not in (None, "-") else sys.stdin.read()
                result = pl.save_bible(args.plan_id, json.loads(raw), args.expect_updated)
            elif c == "regen":
                pl._set(args.plan_id, busy_step="regen", pid=__import__("os").getpid())
                result = pl.regen(args.plan_id, args.target, args.instruction, args.model)
            elif c == "sources":
                result = pl.sources(args.plan_id)
            elif c == "lore-search":
                result = pl.lore_search(args.plan_id, args.query, args.lang)
            elif c == "approve":
                result = pl.approve(args.plan_id, args.slug)
            elif c == "model":
                result = pl.set_model(args.plan_id, args.model)
            elif c == "show":
                result = pl.get(args.plan_id)
            else:
                result = pl.list()
        elif args.command == "publish":
            from shortclip.publish import Accounts, Poster, default_texts
            acc = Accounts(ROOT)
            c = args.pub_cmd
            if c == "accounts":
                result = acc.status()
            elif c == "set-app":
                result = acc.set_app(args.platform, json.loads(sys.stdin.read() or "{}"))
            elif c == "connect":
                result = acc.connect(args.platform)
            elif c == "finish":
                result = acc.callback(args.platform, state=args.state, code=args.code, pasted_url=args.pasted_url)
            elif c == "disconnect":
                result = acc.disconnect(args.platform)
            elif c == "creator":
                result = acc.tiktok_creator()
            elif c == "defaults":
                result = default_texts(pipe.con, args.episode_id)
            else:
                poster = Poster(ROOT, pipe.con, acc)
                if c == "new":
                    result = poster.create(args.episode_id, args.platform, json.loads(args.options_json), again=args.again)
                    if args.run:
                        result = poster.run(result["id"])
                elif c == "run":
                    result = poster.run(args.post_id)
                elif c == "refresh":
                    result = poster.refresh(args.post_id)
                else:
                    result = poster.list(args.episode)
        elif args.command == "models":
            from shortclip.external import load_models, set_models
            changes = dict(x.split("=", 1) for x in args.set)
            m = set_models(ROOT, changes) if changes else load_models(ROOT)
            result = {"roles": {r: m.get(r, m["default"]) for r in m.get("roles", {})}, "choices": m.get("choices", [])}
        elif args.command == "voice":
            result = pipe.set_voice(args.job_id, {"engine": args.engine, "voice": args.voice, "speed": args.speed})
        elif args.command == "series":
            if args.rules_json or args.voice_json or args.title or args.genre:
                result = pipe.set_series(args.slug,
                                         rules=json.loads(args.rules_json) if args.rules_json else None,
                                         voice=json.loads(args.voice_json) if args.voice_json else None,
                                         title=args.title, genre=args.genre)
            else:
                result = pipe.series_settings(args.slug)
        elif args.command == "status":
            result = pipe.status(args.job)
        elif args.command == "advance":
            result = pipe.advance(args.job_id, "approve" if args.approve else "reject", args.reject or "")
        elif args.command == "resume":
            result = pipe.resume(args.job_id)
        elif args.command == "thumb":
            from shortclip import thumbnail as T
            epdir = pipe.episode_dir(args.episode_id)
            c = args.thumb_cmd
            if c == "frames":
                result = {"frames": T.frames(epdir, force=args.force)}
            elif c == "ideas":
                result = pipe.thumb_ideas(args.episode_id, args.count, args.extra, args.model)
            elif c == "render":
                result = T.render(epdir, json.loads(args.spec_json))
            elif c == "select":
                result = T.select(epdir, args.file)
            elif c == "delete":
                T.delete(epdir, args.file)
                result = T.state(epdir)
            else:
                result = T.state(epdir)
        elif args.command == "redo":
            beats = None if args.beats is None else [b for b in args.beats.split(",") if b.strip()]
            result = pipe.redo(args.job_id, args.from_role, args.reason, fresh=args.fresh, beats=beats,
                               run=not args.no_run)
        elif args.command == "edit-script":
            result = pipe.edit_script(args.job_id, json.loads(sys.stdin.read()), run=not args.no_run)
        elif args.command == "cancel":
            result = pipe.cancel(args.job_id)
        else:
            result = pipe.list_jobs(args.limit)
        as_json = args.json or getattr(args, "sub_json", False)
        emit(result, as_json)
        return 0
    except (StepError, ValidationError, OSError, ValueError, RuntimeError) as exc:  # PlanError=ValueError, PublishError=RuntimeError
        if args.json or getattr(args, "sub_json", False):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print("ERROR:", exc, file=sys.stderr)
        return 1
    finally:
        pipe.close()


if __name__ == "__main__":
    ensure_utf8_mode()
    sys.exit(main())
