#!/usr/bin/env python3
"""lore_research.py — ค้นข้อมูลจริง (สถานที่ ตำนาน บุคคลในประวัติศาสตร์) จาก Wikipedia ไทย/อังกฤษ

ใช้โดย agent วางเรื่อง: ทุก URL ที่ใช้อ้างอิงใน lore ต้องมาจากเครื่องมือนี้ เพราะระบบตรวจว่า URL อยู่ใน
ไฟล์ cache (`--cache`) จริง ถ้าไม่อยู่ lore ข้อนั้นจะถูกตัดทิ้ง (กันการแต่งข้อเท็จจริง/แหล่งที่มา)

ใช้:  python3 tools/lore_research.py --cache <dir>/sources.jsonl "แม่นาคพระโขนง" ["ตลาดน้อย" ...]
ผล:  พิมพ์ JSON ต่อผลลัพธ์ {query, lang, title, url, extract} และต่อท้ายลง cache
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "AIShortClipLoreResearch/1.0 (personal project; pinto)"


def _get(url: str, timeout=15, tries=4):
    """Wikipedia answers bursts with 429: wait (Retry-After or 5/15/40 s) instead of failing the search."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503) or attempt == tries - 1:
                raise
            wait = exc.headers.get("Retry-After")
            time.sleep(min(60, int(wait)) if wait and wait.isdigit() else (5, 15, 40)[attempt])


def search(query: str, lang: str, limit: int = 3) -> list[dict]:
    q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": query,
                                "format": "json", "srlimit": limit})
    hits = _get(f"https://{lang}.wikipedia.org/w/api.php?{q}").get("query", {}).get("search", [])
    out = []
    for h in hits:
        title = h["title"]
        try:
            s = _get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                     + urllib.parse.quote(title.replace(" ", "_")))
        except Exception:  # noqa: BLE001 - one missing summary must not kill the search
            continue
        extract = (s.get("extract") or "").strip()
        if not extract:
            continue
        url = (s.get("content_urls") or {}).get("desktop", {}).get("page") or \
            f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        out.append({"query": query, "lang": lang, "title": title, "url": url, "extract": extract[:900]})
        time.sleep(0.6)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="+")
    ap.add_argument("--cache", required=True, help="jsonl file collecting every fetched source")
    ap.add_argument("--lang", default="th,en")
    ap.add_argument("--limit", type=int, default=3)
    args = ap.parse_args()
    results = []
    for q in args.queries[:8]:
        for lang in args.lang.split(","):
            try:
                results += search(q, lang.strip(), args.limit)
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"query": q, "lang": lang, "error": str(exc)[:200]}, ensure_ascii=False))
    with open(args.cache, "a", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    if not results:
        print(json.dumps({"note": "no results; try other words (Thai and English)"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
