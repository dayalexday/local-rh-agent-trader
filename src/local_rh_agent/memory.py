"""Inspectable memory: one JSON object per line, plus a markdown rendering."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload)


def append_capped(path: Path, row: dict[str, Any], *, limit: int = 36) -> list[dict[str, Any]]:
    rows = load_jsonl(path)
    rows.append(row)
    if len(rows) > limit:
        rows = rows[-limit:]
    write_jsonl(path, rows)
    return rows


def render_lessons(lessons: list[dict[str, Any]]) -> str:
    if not lessons:
        return "# Lessons\n\nNo lessons yet.\n"
    lines = ["# Lessons", ""]
    for lesson in lessons:
        stamp = lesson.get("scored_on") or lesson.get("as_of") or "undated"
        lines.append(f"## {stamp}")
        lines.append("")
        lines.append(str(lesson.get("lesson") or "").strip())
        commentary = str(lesson.get("model_commentary") or "").strip()
        if commentary:
            lines.append("")
            lines.append(f"Commentary: {commentary}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def record_decision(path: Path, decision: dict[str, Any]) -> None:
    """Keep the latest decision for a given as-of date."""
    rows = [row for row in load_jsonl(path) if row.get("as_of") != decision.get("as_of")]
    rows.append(decision)
    write_jsonl(path, rows)
