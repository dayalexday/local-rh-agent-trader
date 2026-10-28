"""Saved research memos: one JSON sidecar and one markdown page per agent."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Memo:
    agent: str
    as_of: str
    title: str
    summary: str
    reasoning: str
    facts: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    commentary_source: str = "fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_memo(directory: Path, memo: Memo, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{memo.agent}.json").write_text(
        _dump(memo.to_dict())
    )
    ignored = memo.facts.get("ignored_tickers") or []
    ignored_line = ""
    if ignored:
        ignored_line = (
            "\n\nTickers mentioned only in commentary and ignored: "
            + ", ".join(str(item) for item in ignored)
            + "."
        )
    model_summary = str(memo.facts.get("model_summary") or "").strip()
    commentary = memo.reasoning.strip()
    if model_summary:
        commentary = f"{model_summary}\n\n{commentary}"
    markdown = (
        f"# {memo.title}\n\n"
        f"**As of:** {memo.as_of}  \n"
        f"**Provider:** {memo.provider} / `{memo.model}`  \n"
        f"**Commentary source:** {memo.commentary_source}\n\n"
        f"## Summary\n\n{memo.summary}\n\n"
        f"{body.rstrip()}\n\n"
        f"## Model commentary\n\n{commentary}{ignored_line}\n\n"
        "_Ranks, scores, weights, and orders are computed in Python. "
        "Commentary cannot change them._\n"
    )
    (directory / f"{memo.agent}.md").write_text(markdown)


def _dump(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, indent=2) + "\n"
