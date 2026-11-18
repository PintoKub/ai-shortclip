#!/usr/bin/env python3
"""tts_natural.py — edge-tts แบบพากย์ต่อเนื่อง + ใส่อารมณ์ตามบีท + ซับตรงคำจริง

ทำไมต้องมีตัวนี้: build_ep.py อ่านทีละบรรทัด = 18 คำขอแยกกัน ทุกบรรทัดจึงลงท้าย
ประโยคและมีเงียบหัว/ท้าย ~1 วิ/บรรทัด → ฟังเป็น "พูด ๆ หยุด ๆ" ไม่เป็นธรรมชาติ

วิธีของตัวนี้:
  1. รวมทุกบรรทัดในบีทเป็นข้อความเดียว คั่นด้วย " … " (ลมหายใจ) → prosody ไหลต่อเนื่อง
  2. ปรับ --rate ต่อบีท: hook เร็ว, escalate เร่งขึ้น, twist/cliff ช้าลง = อารมณ์
  3. ต่อบีทเป็นแทร็กเดียวโดยไม่ตัดเสียงเลย (ไม่มีรอยต่อกลางคำ)
  4. ถอดเสียงด้วย faster-whisper (word timestamps) แล้ว align กับบทที่รู้อยู่แล้ว
     → ได้เวลาเริ่ม/จบของทุกบรรทัดแบบตรงคำจริง ใช้ทำซับ
  5. ผสม SFX + BGM + loudnorm -14 LUFS

ใช้: python3 tools/tts_natural.py projects/<series>/<ep>
ผล: audio/voice.wav, audio/mix.wav, audio/timing_raw.json, shots.json
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time

import shutil

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDGE = [sys.executable, "-m", "edge_tts"]            # pip install edge-tts (no API key)
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE") or shutil.which("ffprobe") or "ffprobe"
EARSHOT = os.environ.get("EARSHOT") or shutil.which("earshot")   # optional: only used to synthesise SFX that are not bundled
PY_VIDEO = sys.executable                             # faster-whisper + pythainlp are imported by this interpreter
SFX_LIB = os.path.join(ROOT, "assets", "sfx")         # bundled sound effects: <name>.wav (hook, twist, cliff, swish-*, twist-sub, end-*)

LEAD = 0.35
BEAT_GAP = 0.85
TAIL = 4.20
MIN_TOTAL = 51.0   # QA floor is 50 s; keep 1 s margin
TITLE_END_MAX = 3.15   # validate_shots allows 3.2 s for the title card
TITLE_END_TARGET = 2.85
MAX_TAIL = 8.0     # longest end card we accept to reach MIN_TOTAL
MAX_PAUSE = float(os.environ.get("MAX_PAUSE", "0.45"))  # เพดานช่วงเงียบในบีท
PAUSE_MARK = " … "

# edge-tts สาธารณะ: --rate เสถียร (--pitch ทำให้ NoAudioReceived บ่อย) → ใช้อารมณ์ผ่าน rate
PROSODY = {
    "hook":     "+13%",
    "setup":    "+3%",
    "escalate": "+8%",
    "twist":    "-3%",
    "cliff":    "-9%",
}
BEAT_ORDER = ["hook", "setup", "escalate", "twist", "cliff"]

SFX_BY_BEAT = {
    "hook":  [("impact/deep", 0.05, 0.42)],
    "twist": [("impact/swell", -0.55, 0.32)],
    "cliff": [("cut_hit/rumble", 0.00, 0.34)],
}
# เสียงเฉพาะบรรทัด: อ่านจาก script.json["sfx_cues"] = {"l07": "impact/deep"} (ไม่บังคับ)


def sfx_cues(script):
    if isinstance(script.get("sfx_cues"), dict):
        return dict(script["sfx_cues"])
    return {}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def dur(path):
    r = run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def norm(s):
    """ตัดช่องว่าง/เครื่องหมายวรรคตอนออกให้เหลือตัวอักษรล้วน เพื่อ align"""
    return re.sub(r"[\s\u200b…,.!?\"'“”‘’\-–—:;()\[\]]+", "", s)


def edge_say(text, out_wav, voice, rate, retries=9):
    """edge-tts สาธารณะ throttle แบบ IP/เวลา: ต้องถอยให้ไกล (45s → 180s)"""
    mp3 = out_wav + ".src.mp3"
    delay = 45
    for attempt in range(1, retries + 1):
        if os.path.exists(mp3):
            os.remove(mp3)
        r = run([*EDGE, "--voice", voice, f"--rate={rate}", "--text", text, "--write-media", mp3])
        if r.returncode == 0 and os.path.exists(mp3) and os.path.getsize(mp3) > 2000:
            break
        if attempt == retries:
            raise SystemExit(f"edge-tts failed for {os.path.basename(out_wav)}: {r.stderr[-300:]}")
        print(f"    throttle -> sleep {delay}s", flush=True)
        time.sleep(delay)
        delay = min(int(delay * 1.6), 180)
    r = run([FFMPEG, "-y", "-loglevel", "error", "-i", mp3,
             "-af", "loudnorm=I=-16:TP=-2:LRA=11,aresample=48000",
             "-ac", "1", "-ar", "48000", out_wav])
    os.remove(mp3)
    if r.returncode != 0:
        raise SystemExit("ffmpeg decode failed: " + r.stderr[-300:])


def trim_edges(path, head=0.04, tail=0.10):
    tmp = path + ".trim.wav"
    af = (f"silenceremove=start_periods=1:start_threshold=-45dB:start_silence={head},"
          f"areverse,"
          f"silenceremove=start_periods=1:start_threshold=-45dB:start_silence={tail},"
          f"areverse")
    r = run([FFMPEG, "-y", "-loglevel", "error", "-i", path, "-af", af, tmp])
    if r.returncode == 0 and os.path.getsize(tmp) > 4000:
        os.replace(tmp, path)
    elif os.path.exists(tmp):
        os.remove(tmp)


def silences(path, noise="-45dB", min_len=0.45):
    r = run([FFMPEG, "-i", path, "-af", f"silencedetect=noise={noise}:d={min_len}",
             "-f", "null", "-"])
    out, cur = [], None
    for line in r.stderr.splitlines():
        if "silence_start:" in line:
            cur = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and cur is not None:
            out.append((cur, float(line.split("silence_end:")[1].split()[0])))
            cur = None
    return out


def compress_pauses(src, dst, max_pause=0.42, noise="-45dB"):
    """ย่นช่วงเงียบที่ยาวเกิน max_pause ให้เหลือ max_pause (ตัดกลาง silence ไม่กระทบคำ)

    คืน list ของ (ตำแหน่งในไฟล์เดิม, วินาทีที่ถูกตัดสะสม) ใช้เลื่อนเวลาซับ
    """
    d = dur(src)
    segs, shifts, prev, removed = [], [], 0.0, 0.0
    for s, e in silences(src, noise, max_pause):
        if e - s <= max_pause + 0.02:
            continue
        segs.append((prev, s + max_pause))
        removed += (e - s) - max_pause
        shifts.append((s + max_pause, removed))
        prev = e
    segs.append((prev, d))
    if not shifts:
        if src != dst:
            run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-ar", "48000", "-ac", "1", dst])
        return []

    parts, labels = [], []
    for i, (a, b) in enumerate(segs):
        ln = max(0.02, b - a)
        f = f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS"
        if i > 0:
            f += ",afade=t=in:st=0:d=0.02"
        if i < len(segs) - 1:
            f += f",afade=t=out:st={max(0,ln-0.02):.3f}:d=0.02"
        parts.append(f + f"[v{i}]")
        labels.append(f"[v{i}]")
    fc = ";".join(parts) + ";" + "".join(labels) + \
        f"concat=n={len(segs)}:v=0:a=1[out]"
    r = run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-filter_complex", fc,
             "-map", "[out]", "-ar", "48000", "-ac", "1", dst])
    if r.returncode != 0:
        raise SystemExit("compress_pauses failed: " + r.stderr[-400:])
    return shifts


def shift_time(t, shifts):
    d = 0.0
    for pos, cum in shifts:
        if t >= pos:
            d = cum
        else:
            break
    return max(0.0, t - d)


def sfx(ep, sid, name):
    d = os.path.join(ep, "audio", "sfx")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{name}.wav")
    if os.path.exists(p) and os.path.getsize(p) > 2000:
        return p
    for key in (name, sid.replace("/", "__")):         # bundled effect: no external tool needed
        lib = os.path.join(SFX_LIB, key + ".wav")
        if os.path.exists(lib):
            shutil.copyfile(lib, p)
            return p
    if EARSHOT:                                        # optional: synthesise an effect that is not bundled
        run([EARSHOT, "sfx", "render", sid, "-o", p])
    return p if os.path.exists(p) and os.path.getsize(p) > 2000 else None


def make_bgm(ep, total):
    path = os.path.join(ep, "audio", "bgm_bed.wav")
    fc = ("[0:a]volume=0.5[a];[1:a]volume=0.22[b];[2:a]lowpass=f=220,volume=0.5[c];"
          "[a][b][c]amix=inputs=3:normalize=0,tremolo=f=0.12:d=0.55,volume=0.55,"
          f"afade=t=in:st=0:d=1.5,afade=t=out:st={max(0,total-3)}:d=3[out]")
    run([FFMPEG, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=55:duration={total}",
         "-f", "lavfi", "-i", f"sine=frequency=82.5:duration={total}",
         "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={total}:amplitude=0.35",
         "-filter_complex", fc, "-map", "[out]", "-ar", "48000", "-ac", "2", path])
    return path


WHISPER_PY = r'''
import json, sys
from faster_whisper import WhisperModel
wav, out, prompt = sys.argv[1], sys.argv[2], open(sys.argv[3]).read()
m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=8)
segs, _ = m.transcribe(wav, language="th", word_timestamps=True, vad_filter=False,
                       beam_size=5, initial_prompt=prompt, condition_on_previous_text=False)
words = []
for s in segs:
    for w in (s.words or []):
        words.append({"w": w.word, "s": round(w.start, 3), "e": round(w.end, 3)})
json.dump(words, open(out, "w"), ensure_ascii=False)
print(json.dumps({"words": len(words)}))
'''


def whisper_words(wav, out_json, prompt_text):
    """ถอดเสียงพร้อม initial_prompt = บทจริง → ความแม่นสูงมาก (TTS เสียงสะอาด)"""
    pp = out_json + ".prompt.txt"
    open(pp, "w").write(prompt_text)
    r = run([PY_VIDEO, "-c", WHISPER_PY, wav, out_json, pp])
    if r.returncode != 0 or not os.path.exists(out_json):
        raise SystemExit("whisper failed: " + (r.stderr or "")[-500:])
    return json.load(open(out_json))


TOKENIZE_PY = r'''
import json, sys
from pythainlp.tokenize import word_tokenize
texts = json.load(open(sys.argv[1]))
out = [[w for w in word_tokenize(t, engine="newmm", keep_whitespace=False) if w.strip()] for t in texts]
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False)
'''


def tokenize_lines(texts, workdir):
    """ตัดคำไทยด้วย pythainlp (venv วิดีโอ) — ล้มเหลวเมื่อไรใช้การแบ่งตามช่องว่างแทน"""
    src, dst = os.path.join(workdir, "tok_in.json"), os.path.join(workdir, "tok_out.json")
    json.dump(texts, open(src, "w"), ensure_ascii=False)
    r = run([PY_VIDEO, "-c", TOKENIZE_PY, src, dst])
    try:
        toks = json.load(open(dst)) if r.returncode == 0 else None
    except (OSError, ValueError):
        toks = None
    for p in (src, dst):
        if os.path.exists(p):
            os.remove(p)
    if not toks or len(toks) != len(texts):
        print("tokenize fallback: split on spaces " + (r.stderr or "")[-200:], flush=True)
        toks = [t.split() for t in texts]
    return toks


def _aligner(full_text, words, token_end=False):
    """คืนฟังก์ชัน time_at(ตำแหน่งอักษรในบท) → วินาที จาก word timestamps ของ whisper"""
    trans = norm("".join(w["w"] for w in words))
    offs, pos = [], 0
    for w in words:
        n = norm(w["w"])
        offs.append((pos, pos + len(n), w["s"], w["e"]))
        pos += len(n)
    full = norm(full_text)
    sm = difflib.SequenceMatcher(None, full, trans, autojunk=False)
    cmap = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            cmap[a + k] = b + k

    def time_at(ci, is_end=False):
        best = None
        for i in range(ci, -1 if is_end else ci - 40, -1 if is_end else 1):
            if i in cmap:
                best = i
                break
        if best is None:
            return None
        ti = cmap[best]
        for i0, i1, s, e in offs:
            if i0 <= ti < max(i1, i0 + 1):
                if is_end and token_end:
                    return e
                frac = (ti - i0) / max(1, i1 - i0)
                return s + (e - s) * frac
        return None
    return full, time_at


def align_lines(full_text, lines, words):
    """map แต่ละบรรทัด → (start, end) จาก word timestamps ด้วย difflib"""
    full, time_at = _aligner(full_text, words)
    out, cursor, char_pos = [], 0.0, 0
    for ln in lines:
        n = norm(ln["text"])
        c0, c1 = char_pos, char_pos + len(n)
        char_pos = c1
        s = time_at(c0) if c0 < len(full) else None
        e = time_at(max(c0, c1 - 1), is_end=True) if c1 > 0 else None
        if s is None:
            s = cursor
        if e is None or e <= s:
            e = s + max(0.9, len(n) * 0.06)
        s = max(s, cursor)
        e = max(e, s + 0.4)
        out.append((round(max(0.0, s - 0.03), 3), round(e + 0.06, 3)))
        cursor = round(e + 0.06, 3)
    return out


def proportional_words(tokens, start, end):
    """แบ่งเวลาคำตามจำนวนตัวอักษร (ใช้เมื่อไม่มี whisper)"""
    lens = [max(1, len(norm(t))) for t in tokens]
    total, acc, out = sum(lens), 0, []
    for t, n in zip(tokens, lens):
        s = start + (end - start) * acc / total
        acc += n
        out.append({"t": t, "s": round(s, 3), "e": round(start + (end - start) * acc / total, 3)})
    return out


def align_words(full_text, lines, words, tokens_per_line, spans):
    """เวลาเริ่ม/จบของทุกคำ (ตัดคำแล้ว) ในบีท — ใช้ทำซับคาราโอเกะ

    เวลาคำถูกบีบให้อยู่ในช่วงของบรรทัด เรียงไม่ถอยหลัง และคำที่ align ไม่ได้ถูกเติมตามสัดส่วนตัวอักษร
    """
    full, time_at = _aligner(full_text, words, token_end=True)
    out, char_pos = [], 0
    for ln, toks, (ls, le) in zip(lines, tokens_per_line, spans):
        line_norm = norm(ln["text"])
        base = char_pos
        char_pos += len(line_norm)
        rows, cur = [], 0
        for t in toks:
            n = norm(t)
            idx = line_norm.find(n, cur) if n else -1
            if idx < 0:
                rows.append({"t": t, "s": None, "e": None, "n": max(1, len(n))})
                continue
            c0, c1 = base + idx, base + idx + len(n)
            cur = idx + len(n)
            s = time_at(c0) if c0 < len(full) else None
            e = time_at(c1 - 1, is_end=True) if c1 - 1 < len(full) else None
            rows.append({"t": t, "s": s, "e": e, "n": max(1, len(n))})
        lo, hi = ls + 0.03, le - 0.06  # เวลาพูดจริงของบรรทัด (ก่อนเผื่อหัวท้าย)
        known = [r for r in rows if r["s"] is not None]
        if len(known) < max(1, len(rows) // 2):
            out.append(proportional_words([r["t"] for r in rows], lo, hi))
            continue
        prev = lo
        for r in rows:
            if r["s"] is not None:
                r["s"] = min(max(r["s"], prev), hi)
                prev = r["s"]
        # เติมคำที่ไม่มีเวลาด้วยการแบ่งช่วงระหว่างเพื่อนบ้านตามจำนวนตัวอักษร
        i = 0
        while i < len(rows):
            if rows[i]["s"] is not None:
                i += 1
                continue
            j = i
            while j < len(rows) and rows[j]["s"] is None:
                j += 1
            a = rows[i - 1]["s"] if i > 0 else lo
            b = rows[j]["s"] if j < len(rows) else hi
            chunk = sum(r["n"] for r in rows[i - 1 if i > 0 else i:j])
            acc = rows[i - 1]["n"] if i > 0 else 0
            for k in range(i, j):
                rows[k]["s"] = a + (b - a) * acc / max(1, chunk)
                acc += rows[k]["n"]
            i = j
        res = []
        for k, r in enumerate(rows):
            nxt = rows[k + 1]["s"] if k + 1 < len(rows) else hi
            e = nxt if k + 1 < len(rows) else max(hi, r["s"] + 0.12)
            res.append({"t": r["t"], "s": round(r["s"], 3), "e": round(max(e, r["s"] + 0.05), 3)})
        out.append(res)
    return out


# ---- mix v2: voice chain, stereo bed ducked under the voice, end sting, dynamics-preserving master ----
VOICE_CHAIN = ("highpass=f=85,lowpass=f=13500,"
               "equalizer=f=220:t=q:w=1.1:g=-1.5,"        # less boxy
               "equalizer=f=3200:t=q:w=1.0:g=2.2,"        # presence: Thai consonants read on phone speakers
               "deesser=i=0.35:m=0.5:f=0.5:s=o,"
               "acompressor=threshold=0.1:ratio=3:attack=8:release=140:makeup=1.6:knee=4")
BEAT_SWISH = ("swish/soft", -0.30, 0.20)      # soft air into each beat change (not every cut)
END_STING = [("impact/deep", 0.00, 0.55), ("pro_sub_drop/low", 0.00, 0.40)]
TWIST_SUB = ("pro_sub_drop/soft", -0.10, 0.45)
TARGET_LUFS, TARGET_TP = -14.0, -1.5


def make_bgm_stereo(ep, total, end_at):
    """Dark drone: two detuned low tones + per-channel brown noise (real stereo), slow swell,
    quieter under the story, rising into the end card. Code-made bed (no licence needed)."""
    path = os.path.join(ep, "audio", "bgm_bed.wav")
    fc = ("[0:a]volume=0.45[a];[1:a]volume=0.20[b];[2:a]lowpass=f=240,volume=0.55[nl];"
          "[3:a]lowpass=f=260,volume=0.55[nr];[4:a]volume=0.10,tremolo=f=0.23:d=0.6[hi];"
          "[a][b][hi]amix=inputs=3:normalize=0,tremolo=f=0.11:d=0.5,asplit=2[t1][t2];"
          "[t1][nl]amix=inputs=2:normalize=0[l];[t2][nr]amix=inputs=2:normalize=0[r];"
          "[l][r]join=inputs=2:channel_layout=stereo,"
          f"volume='if(lt(t,{end_at:.3f}),0.55,0.55+0.45*min(1,(t-{end_at:.3f})/0.8))':eval=frame,"
          f"afade=t=in:st=0:d=1.2,afade=t=out:st={max(0, total - 1.6):.3f}:d=1.6[out]")
    run([FFMPEG, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=55:duration={total}",
         "-f", "lavfi", "-i", f"sine=frequency=82.9:duration={total}",
         "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={total}:amplitude=0.35:seed=11",
         "-f", "lavfi", "-i", f"anoisesrc=color=brown:duration={total}:amplitude=0.35:seed=29",
         "-f", "lavfi", "-i", f"sine=frequency=659:duration={total}",
         "-filter_complex", fc, "-map", "[out]", "-ar", "48000", "-ac", "2", path])
    return path


def _measure_loudnorm(path):
    r = run([FFMPEG, "-hide_banner", "-i", path, "-af",
             f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA=11:print_format=json", "-f", "null", "-"])
    txt = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
    return json.loads(txt)


def build_mix(ep, script, rows, total_dur, voice):
    """voice (processed, centre) + SFX (panned) + stereo bed ducked by the voice → 2-pass linear master."""
    end_at = round(rows[-1]["end"] + 0.4, 3) if rows else total_dur
    events = []  # (sfx id, time, gain, file name, pan -1..1)
    for beat, items in SFX_BY_BEAT.items():
        hit = [r_ for r_ in rows if r_["beat"] == beat]
        if hit:
            for sid, off, vol in items:
                events.append((sid, max(0.0, hit[0]["start"] + off), vol, beat, 0.0))
    starts = {}
    for r_ in rows:
        starts.setdefault(r_["beat"], r_["start"])
    for n, (beat, t0) in enumerate(sorted(starts.items(), key=lambda kv: kv[1])[1:]):
        sid, off, vol = BEAT_SWISH
        events.append((sid, max(0.0, t0 + off), vol, f"swish-{beat}", -0.45 if n % 2 else 0.45))
    twist = [r_ for r_ in rows if r_["beat"] == "twist"]
    if twist:
        sid, off, vol = TWIST_SUB
        events.append((sid, max(0.0, twist[0]["start"] + off), vol, "twist-sub", 0.0))
    for sid, off, vol in END_STING:
        events.append((sid, end_at + off, vol, "end-" + sid.split("/")[-1], 0.0))
    for vid, sid in sfx_cues(script).items():
        hit = [r_ for r_ in rows if r_["id"] == vid]
        if hit:
            events.append((sid, hit[0]["start"], 0.42,
                           f"bell-{vid}" if "bell" in sid else f"cue-{vid}", 0.25))

    inputs = [voice]
    # apad: voice.wav ends with the last word; without padding the sidechain stops the bed there and
    # the end card loses its music (mix came out shorter than the timeline).
    filt = [f"[0:a]apad=whole_dur={total_dur},{VOICE_CHAIN},asplit=2[vo][vkey]"]
    sfx_labels = []
    for sid, at, vol, name, pan in events:
        p = sfx(ep, sid, name)
        if not p:
            continue
        idx = len(inputs)
        inputs.append(p)
        lg, rg = (1 - max(0, pan)), (1 + min(0, pan))
        filt.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=mono,adelay={int(round(at * 1000))}:all=1,"
                    f"volume={vol},pan=stereo|c0={lg:.2f}*c0|c1={rg:.2f}*c0[s{idx}]")
        sfx_labels.append(f"[s{idx}]")
    bgm = make_bgm_stereo(ep, total_dur, end_at)
    idx = len(inputs)
    inputs.append(bgm)
    filt.append(f"[{idx}:a]volume=0.30[bed]")
    # Bed dips ~7 dB while the narrator speaks and comes back in pauses and on the end card.
    filt.append("[vkey]aformat=channel_layouts=stereo[key]")
    filt.append("[bed][key]sidechaincompress=threshold=0.03:ratio=5:attack=25:release=420:makeup=1[bedd]")
    filt.append("[vo]aformat=channel_layouts=stereo[vst]")
    labels = ["[vst]", "[bedd]"] + sfx_labels
    filt.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,"
                f"alimiter=limit=0.84:attack=3:release=60:level=false[out]")
    pre = os.path.join(ep, "audio", "mix_pre.wav")
    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-filter_complex", ";".join(filt), "-map", "[out]", "-t", str(total_dur),
            "-ar", "48000", "-ac", "2", pre]
    r = run(cmd)
    if r.returncode != 0:
        raise SystemExit("mix failed:\n" + r.stderr[-800:])
    # Two-pass linear loudnorm keeps the twist/end hits above the body (one-pass flattens them).
    m = _measure_loudnorm(pre)
    mixp = os.path.join(ep, "audio", "mix.wav")
    af = (f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA=11:measured_I={m['input_i']}:"
          f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true,aresample=48000")
    r = run([FFMPEG, "-y", "-loglevel", "error", "-i", pre, "-af", af, "-ar", "48000", "-ac", "2", mixp])
    if r.returncode != 0:
        raise SystemExit("master failed:\n" + r.stderr[-800:])
    os.remove(pre)
    return mixp


def remix_only(ep):
    """Rebuild audio/mix.wav from existing voice.wav + timing_raw.json (no TTS, no whisper)."""
    script = json.load(open(os.path.join(ep, "script.json")))
    rows = json.load(open(os.path.join(ep, "audio", "timing_raw.json")))["lines"]
    total = json.load(open(os.path.join(ep, "shots.json")))["duration"]
    mixp = build_mix(ep, script, rows, total, os.path.join(ep, "audio", "voice.wav"))
    print(json.dumps({"remix": mixp, "duration": total, "mix_duration": round(dur(mixp), 3)}))
    return 0



# ---- voice engines: edge-tts (cloud) or Wayu (local daemon) ----
WAYU_DIR = os.environ.get("WAYU_DIR") or os.path.join(HOME, ".local/share/wayu-tts")   # optional local Thai TTS daemon (CC-BY-NC); edge is the default
DEFAULT_VOICE = {"engine": "edge", "voice": "th-TH-PremwadeeNeural", "speed": 1.0}
WAYU_VOICES = ["f_young_warm", "f_young_clear", "f_young_bright", "f_teen_bright", "f_mid_clear", "f_mid_warm",
               "f_elderly_soft", "f_elderly_low", "m_young_clear", "m_teen_bright", "m_mid_warm", "m_elderly_deep"]
EDGE_VOICES = ["th-TH-PremwadeeNeural", "th-TH-NiwatNeural"]


def normalize_voice(cfg):
    cfg = {**DEFAULT_VOICE, **(cfg or {})}
    if cfg["engine"] not in ("edge", "wayu"):
        raise SystemExit(f"unknown voice engine {cfg['engine']!r}")
    allowed = WAYU_VOICES if cfg["engine"] == "wayu" else EDGE_VOICES
    if cfg["voice"] not in allowed:
        cfg["voice"] = allowed[0]
    cfg["speed"] = round(min(2.0, max(0.6, float(cfg.get("speed") or 1.0))), 3)
    return {"engine": cfg["engine"], "voice": cfg["voice"], "speed": cfg["speed"]}


def beat_speed(cfg, beat):
    """Per-beat emotion. edge: the measured EP.1v2 rates. Wayu: same ratios, anchored so the HOOK
    plays at exactly the chosen speed (what the user auditioned) and later beats slow down like EP.1v2."""
    pct = float(PROSODY.get(beat, "+0%").rstrip("%"))
    if cfg["engine"] == "edge":
        return PROSODY.get(beat, "+0%")
    hook = float(PROSODY["hook"].rstrip("%"))
    return round(cfg["speed"] * (1 + pct / 100) / (1 + hook / 100), 3)


def _wayu_client():
    import importlib.util
    spec = importlib.util.spec_from_file_location("wayu_client", os.path.join(WAYU_DIR, "wayu_tts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wayu_say(text, out_wav, voice, speed, seed=7):
    """Ask the warm Wayu daemon (unix socket) for one clip; 24 kHz → 48 kHz mono, -16 LUFS like edge_say."""
    import socket
    client = _wayu_client()
    if not client.wait_ready(2):
        client.start_daemon()
        if not client.wait_ready(120):
            raise SystemExit("wayu daemon did not start")
    raw = out_wav + ".wayu.wav"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(600)
    sock.connect(client.SOCK)
    with sock, sock.makefile("rwb") as io:
        io.write((json.dumps({"text": text, "voice": voice, "output": raw, "speed": speed, "seed": seed}) + "\n").encode())
        io.flush()
        reply = json.loads(io.readline() or b"{}")
    if not reply.get("ok"):
        raise SystemExit("wayu failed: " + str(reply.get("error")))
    r = run([FFMPEG, "-y", "-loglevel", "error", "-i", raw,
             "-af", "loudnorm=I=-16:TP=-2:LRA=11,aresample=48000", "-ac", "1", "-ar", "48000", out_wav])
    os.remove(raw)
    if r.returncode != 0:
        raise SystemExit("ffmpeg decode failed: " + r.stderr[-300:])


def say(cfg, beat, text, out_wav):
    if cfg["engine"] == "wayu":
        # Wayu splits sentences at "…" and inserts its own 0.25 s join; a plain space keeps one flowing take,
        # which is what the user auditioned.
        wayu_say(text.replace(PAUSE_MARK, " "), out_wav, cfg["voice"], beat_speed(cfg, beat))
    else:
        edge_say(text, out_wav, cfg["voice"], beat_speed(cfg, beat))


def load_voice_cfg(ep, cli_voice=None):
    path = os.path.join(ep, "voice.json")
    if os.path.exists(path):
        return normalize_voice(json.load(open(path)))
    if cli_voice:
        engine = "wayu" if cli_voice in WAYU_VOICES else "edge"
        return normalize_voice({"engine": engine, "voice": cli_voice})
    return normalize_voice(None)



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ep")
    ap.add_argument("--voice", default=None, help="voice name (edge or wayu); voice.json in the episode wins")
    ap.add_argument("--script", default="script.json")
    ap.add_argument("--force", action="store_true", help="สร้างเสียงใหม่ทุกบีท")
    ap.add_argument("--remix", action="store_true", help="ผสมเสียงใหม่จาก voice.wav + timing เดิม (ไม่เรียก TTS/whisper)")
    args = ap.parse_args()
    ep = os.path.abspath(args.ep)
    if args.remix:
        return remix_only(ep)
    script = json.load(open(os.path.join(ep, args.script)))
    lines = script["lines"]

    beats = {}
    for ln in lines:
        beats.setdefault(ln["beat"], []).append(ln)
    order = [b for b in BEAT_ORDER if b in beats] + [b for b in beats if b not in BEAT_ORDER]

    os.makedirs(os.path.join(ep, "audio", "beats"), exist_ok=True)
    cfg = load_voice_cfg(ep, args.voice)
    stamp = os.path.join(ep, "audio", "beats", "_voice.json")
    old = json.load(open(stamp)) if os.path.exists(stamp) else None
    if old is None and any(f.endswith(".wav") for f in os.listdir(os.path.join(ep, "audio", "beats"))):
        old = normalize_voice(None)  # beats from before stamps existed were edge Premwadee
    if old is not None and old != cfg:
        bk = os.path.join(ep, "backup", "beats-" + "-".join(str(v) for v in old.values()).replace("/", "_"))
        os.makedirs(bk, exist_ok=True)
        for f in os.listdir(os.path.join(ep, "audio", "beats")):
            if f.endswith(".wav"):
                os.replace(os.path.join(ep, "audio", "beats", f), os.path.join(bk, f))
        print(f"voice changed {old} -> {cfg}; old beats moved to {bk}", flush=True)
        for f in ("_hook_boost.json",):
            if os.path.exists(os.path.join(ep, "audio", "beats", f)):
                os.replace(os.path.join(ep, "audio", "beats", f), os.path.join(bk, f))
        # Switching back to a voice used before: reuse its backed-up beats instead of calling TTS again.
        prev = os.path.join(ep, "backup", "beats-" + "-".join(str(v) for v in cfg.values()).replace("/", "_"))
        if os.path.isdir(prev):
            for f in os.listdir(prev):
                if f.endswith(".wav") or f == "_hook_boost.json":
                    os.replace(os.path.join(prev, f), os.path.join(ep, "audio", "beats", f))
            print(f"restored beats for {cfg} from {prev}", flush=True)
    json.dump(cfg, open(stamp, "w"))
    print(f"voice: {cfg}", flush=True)
    beat_files, beat_starts, full_parts, beat_specs = [], [], [], []
    cursor = LEAD

    for beat in order:
        group = beats[beat]
        text = PAUSE_MARK.join(ln["text"] for ln in group)
        rate = beat_speed(cfg, beat)
        bp = os.path.join(ep, "audio", "beats", f"{beat}.wav")
        fresh = False
        if os.path.exists(bp) and os.path.getsize(bp) > 100000 and not args.force:
            print(f"[{beat}] reuse {bp}", flush=True)
        else:
            print(f"[{beat}] {len(group)} lines {cfg['engine']} {cfg['voice']} rate={rate}", flush=True)
            say(cfg, beat, text, bp)
            trim_edges(bp)
            fresh = True
            if beat == order[0]:  # a new hook take has not been sped up yet
                m = os.path.join(ep, "audio", "beats", "_hook_boost.json")
                if os.path.exists(m):
                    os.remove(m)
        # ย่นช่วงเงียบในบีท (ทำซ้ำได้ ไม่เปลี่ยนอะไรถ้าเงียบสั้นกว่าเพดานอยู่แล้ว)
        raw = bp + ".raw.wav"
        os.replace(bp, raw)
        compress_pauses(raw, bp, MAX_PAUSE)
        os.remove(raw)
        d = dur(bp)
        beat_files.append((bp, cursor))
        beat_starts.append((beat, cursor, d))
        beat_specs.append({"beat": beat, "wav": bp, "start": cursor, "text": text,
                           "group": group, "dur": d})
        cursor += d + BEAT_GAP
        full_parts.append(text)
        if fresh and cfg["engine"] == "edge":
            time.sleep(12)  # เว้นจังหวะกัน 403 จาก edge

    # A fast voice can finish under the 50 s floor: hold the end card longer instead of slowing the speech.
    speech_end = cursor - BEAT_GAP
    total_dur = round(max(speech_end + TAIL, min(MIN_TOTAL, speech_end + MAX_TAIL)), 3)
    if total_dur < 50.0:
        raise SystemExit(f"narration ends at {speech_end:.1f}s; even a {MAX_TAIL}s end card gives {total_dur:.1f}s < 50s "
                         "— the script is too short for this voice speed (lower the speed or add lines)")

    # ---- ต่อบีทเป็นแทร็กเสียงเดียว ----
    inputs, filt, labels = [], [], []
    for i, (bp, at) in enumerate(beat_files):
        inputs.append(bp)
        filt.append(f"[{i}:a]aresample=48000,adelay={int(round(at*1000))}:all=1[v{i}]")
        labels.append(f"[v{i}]")
    fc = ";".join(filt) + ";" + "".join(labels) + \
        f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,volume=1.0[m]"
    voice = os.path.join(ep, "audio", "voice.wav")
    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-filter_complex", fc, "-map", "[m]", "-t", str(total_dur),
            "-ar", "48000", "-ac", "1", voice]
    r = run(cmd)
    if r.returncode != 0:
        raise SystemExit("voice concat failed:\n" + r.stderr[-600:])

    # ---- เวลาซับ: align ต่อบีท (prompt = บทจริงของบีทนั้น) ----
    tokens = tokenize_lines([ln["text"] for ln in lines], os.path.join(ep, "audio"))
    tok_by_id = {ln["id"]: t for ln, t in zip(lines, tokens)}
    spans, word_rows, src = [], [], "whisper-per-beat"
    for spec in beat_specs:
        group, bp, base = spec["group"], spec["wav"], spec["start"]
        words = None
        try:
            words = whisper_words(bp, os.path.join(ep, "audio", f"words_{spec['beat']}.json"),
                                  spec["text"])
            local = align_lines(spec["text"], group, words)
            local = [(s, e) for s, e in local]
        except SystemExit as err:
            print(f"whisper fallback [{spec['beat']}]: {err}", flush=True)
            step = spec["dur"] / max(1, len(group))
            local = [(j * step, (j + 1) * step) for j in range(len(group))]
            src = "proportional"
        # ตรวจความสมเหตุสมผล: บรรทัดยาวผิดปกติ = align หลุด → แบ่งตามสัดส่วนเฉพาะบีทนั้น
        bad = any((e - s) > max(9.0, len(norm(ln["text"])) * 0.30 + 4) for (s, e), ln in zip(local, group))
        if bad:
            step = spec["dur"] / max(1, len(group))
            local = [(j * step, (j + 1) * step) for j in range(len(group))]
            src = "mixed"
            words = None
        group_tokens = [tok_by_id[ln["id"]] for ln in group]
        if words:
            local_words = align_words(spec["text"], group, words, group_tokens, local)
        else:
            local_words = [proportional_words(t, s + 0.03, e - 0.06) for t, (s, e) in zip(group_tokens, local)]
        spans += [(round(base + s, 3), round(base + e, 3)) for s, e in local]
        word_rows += [[{"t": w["t"], "s": round(base + w["s"], 3), "e": round(base + w["e"], 3)} for w in lw]
                      for lw in local_words]

    rows = []
    for ln, (s, e), ws in zip(lines, spans, word_rows):
        rows.append({"id": ln["id"], "beat": ln["beat"], "text": ln["text"],
                     "on_screen": ln.get("on_screen", ""),
                     "start": s, "end": e, "dur": round(e - s, 3), "words": ws})

    # The title line must finish inside the 2.8 s title card (validator allows 3.2 s). A long title
    # ("<series> ตอนที่ห้า <name>") read at the hook rate can overrun: speed up only that part of
    # hook.wav (atempo, no new TTS call), then run the timing again once.
    if rows and rows[0]["end"] > TITLE_END_MAX and lines[0]["beat"] == "hook":
        marker = os.path.join(ep, "audio", "beats", "_hook_boost.json")
        if os.path.exists(marker):
            raise SystemExit(f"title narration still ends at {rows[0]['end']:.2f}s after speeding it up; "
                             "shorten line 1 of the script (title card is 2.8 s)")
        hook_wav = beat_specs[0]["wav"]
        cut = rows[0]["end"] - beat_specs[0]["start"] + 0.02
        factor = round((cut) / (TITLE_END_TARGET - beat_specs[0]["start"]), 3)
        if factor > 1.45:
            raise SystemExit(f"title narration is {cut:.2f}s; it needs x{factor} to fit the 2.8 s title card — "
                             "shorten line 1 of the script")
        tmp = hook_wav + ".boost.wav"
        r = run([FFMPEG, "-y", "-loglevel", "error", "-i", hook_wav, "-filter_complex",
                 f"[0:a]atrim=0:{cut:.3f},asetpts=PTS-STARTPTS,atempo={factor}[a];"
                 f"[0:a]atrim={cut:.3f},asetpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=0:a=1[o]",
                 "-map", "[o]", "-ar", "48000", "-ac", "1", tmp])
        if r.returncode != 0:
            raise SystemExit("title speed-up failed: " + r.stderr[-300:])
        os.replace(tmp, hook_wav)
        json.dump({"factor": factor, "title_end_before": rows[0]["end"]}, open(marker, "w"))
        print(f"title line ended at {rows[0]['end']:.2f}s > {TITLE_END_MAX}s: sped up x{factor}, re-timing", flush=True)
        if os.name == "nt":  # Windows execv detaches: the caller would see this process exit before the redo ends
            sys.exit(subprocess.call([sys.executable, *sys.argv]))
        os.execv(sys.executable, [sys.executable, *sys.argv])
    json.dump({"align": src, "lines": rows},
              open(os.path.join(ep, "audio", "timing_raw.json"), "w"),
              ensure_ascii=False, indent=1)

    # ---- SFX + BGM + mix ----
    mixp = build_mix(ep, script, rows, total_dur, voice)

    # ---- shots.json ----
    visuals = {}
    vpath = os.path.join(ep, "visuals.json")
    if os.path.exists(vpath):
        visuals = json.load(open(vpath))
    shots = []
    for i, r_ in enumerate(rows, 1):
        v = visuals.get(r_["id"], {})
        shots.append({"shot": i, "line": r_["id"], "start": r_["start"], "end": r_["end"],
                      "subtitle": r_["text"], "visual": v.get("visual", ""),
                      "camera": v.get("camera", ""), "on_screen": r_["on_screen"],
                      "words": r_["words"]})
    json.dump({"series": script["series"], "episode": script["episode"],
               "title": script["title"], "duration": total_dur, "fps": 30,
               "width": 1080, "height": 1920, "align": src, "voice": cfg,
               "lines": rows, "shots": shots},
              open(os.path.join(ep, "shots.json"), "w"), ensure_ascii=False, indent=1)

    print(json.dumps({"duration": total_dur, "lines": len(rows), "align": src,
                      "mix": mixp, "mix_duration": round(dur(mixp), 3)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
