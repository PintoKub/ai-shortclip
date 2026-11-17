"""Token usage helpers shared by the pipeline and the database migration."""
from __future__ import annotations

import json

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def merge_usage(usages: list[dict]) -> dict:
    """Sum several Claude passes into one flat object so SQL json_extract works."""
    merged = {key: 0 for key in USAGE_KEYS}
    for usage in usages:
        for key in USAGE_KEYS:
            merged[key] += int((usage or {}).get(key) or 0)
    merged["passes"] = len(usages)
    return merged


def normalize_rows(con) -> int:
    """Rewrite legacy list-shaped steps.usage_json into one merged object."""
    fixed = 0
    for row in con.execute("SELECT id,usage_json FROM steps WHERE usage_json LIKE '[%'").fetchall():
        try:
            items = json.loads(row["usage_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(items, list):
            con.execute("UPDATE steps SET usage_json=? WHERE id=?",
                        (json.dumps(merge_usage([i for i in items if isinstance(i, dict)])), row["id"]))
            fixed += 1
    return fixed
