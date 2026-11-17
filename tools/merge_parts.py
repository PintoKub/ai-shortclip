#!/usr/bin/env python3
"""Merge <stem>.part-N.json files into <stem>.json (dict keys merged, lists concatenated in N order).

The planner writes a big series bible in small pieces because one huge Write call times out at the API.
Usage: merge_parts.py /path/to/plans/7/bible
"""
import json
import re
import sys
from pathlib import Path


def merge(stem: Path) -> dict:
    parts = sorted(stem.parent.glob(stem.name + ".part-*.json"),
                   key=lambda p: int(re.search(r"part-(\d+)", p.name).group(1)))
    if not parts:
        raise SystemExit(f"no {stem.name}.part-N.json files in {stem.parent}")
    out: dict = {}
    for p in parts:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SystemExit(f"{p.name} is not valid JSON: {exc}")
        for k, v in data.items():
            if isinstance(v, list) and isinstance(out.get(k), list):
                out[k] += v
            else:
                out[k] = v
    stem.with_suffix(".json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"parts": len(parts), "keys": sorted(out), "episodes": len(out.get("episodes") or [])}


if __name__ == "__main__":
    print(json.dumps(merge(Path(sys.argv[1])), ensure_ascii=False))
