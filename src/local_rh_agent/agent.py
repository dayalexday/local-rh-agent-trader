"""Ollama tool loop against Robinhood MCP tools plus a few local tools."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from . import config
from .mcp_client import RobinhoodMcp, tool_result_text

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_ORDER_STRING_FIELDS = ("quantity", "dollar_amount", "limit_price", "stop_price", "account_number")


@dataclass
class AgentResult:
    text: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] | None = None


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def parse_text_tool_calls(content: str) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for match in _TOOL_CALL_RE.finditer(content or ""):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        name = payload.get("name") or payload.get("tool")
        arguments = payload.get("arguments") if "arguments" in payload else payload.get("parameters")
        if arguments is None:
            arguments = {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                arguments = {}
        if name and isinstance(arguments, dict):
            calls.append((str(name), arguments))
    return calls


def extract_tool_calls(message: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        name = function.get("name")
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                arguments = {}
        if name and isinstance(arguments, dict):
            calls.append((str(name), arguments))
    if calls:
        return calls
    return parse_text_tool_calls(message.get("content") or "")


def _loads_maybe(text: str) -> Any:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return data
    return data


def extract_agentic_accounts(text: str) -> set[str]:
    """Pull account numbers flagged agentic_allowed from a get_accounts payload."""
    data = _loads_maybe(text)
    if data is None:
        return set()
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            number = node.get("account_number") or node.get("accountNumber")
            allowed = node.get("agentic_allowed")
            if allowed is True and number:
                found.add(str(number))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return found


def is_mutating(name: str) -> bool:
    return name.startswith(config.MUTATING_PREFIXES)


def order_guard(
    name: str,
    arguments: dict[str, Any],
    agentic_accounts: set[str],
) -> str | None:
    """Block order tools until we know the agentic account, and only that account."""
    if name not in config.ORDER_TOOLS:
        return None
    if not agentic_accounts:
        return (
            "Refused. Call get_accounts first and use the account whose "
            "agentic_allowed is true. No other account can be traded."
        )
    account = str(arguments.get("account_number") or "")
    if account not in agentic_accounts:
        allowed = ", ".join(sorted(agentic_accounts))
        return (
            f"Refused. account_number {account or '(missing)'} is not the agentic account. "
            f"Allowed: {allowed}."
        )
    return None


def stable_ref_id(symbol: str, side: str, day: str) -> str:
    key = f"local-rh-agent|{day}|{side.lower()}|{symbol.upper()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _coerce_order_args(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in config.ORDER_TOOLS:
        return arguments
    cleaned = dict(arguments)
    for key in _ORDER_STRING_FIELDS:
        if key in cleaned and cleaned[key] is not None and not isinstance(cleaned[key], str):
            value = cleaned[key]
            if isinstance(value, float) and key == "dollar_amount":
                cleaned[key] = f"{value:.2f}"
            else:
                cleaned[key] = str(value)
    return cleaned


def _shrink(value: Any, limit: int = 280) -> Any:
    """Keep field names, shorten the prose. A 3B context fills up fast."""
    if isinstance(value, dict):
        shrunk: dict[str, Any] = {}
        for key, item in value.items():
            if key == "description" and isinstance(item, str) and len(item) > limit:
                shrunk[key] = item[:limit].rstrip() + "…"
            else:
                shrunk[key] = _shrink(item, limit)
        return shrunk
    if isinstance(value, list):
        return [_shrink(item, limit) for item in value]
    return value


def ollama_tools(mcp_tools: list[dict[str, Any]], extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    converted = []
    for tool in list(mcp_tools) + list(extra or []):
        schema = _shrink(tool.get("inputSchema") or {"type": "object", "properties": {}})
        converted.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": (tool.get("description") or "")[:500],
                "parameters": schema,
            },
        })
    return converted


LOCAL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "write_run_report",
        "description": (
            "Save the rebalance report on this Mac. Call once at the end of a monthly run. "
            "Does not contact Robinhood."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "markdown": {"type": "string", "description": "Human-readable report."},
                "data": {
                    "type": "object",
                    "description": "Machine-readable report. Include kind: monthly_rebalance and date.",
                },
            },
            "required": ["markdown", "data"],
        },
    }
]


def _system_prompt() -> str:
    path = config.PROMPTS_DIR / "system.md"
    base = path.read_text() if path.exists() else "You are a local trading agent."
    mode = "ON — place_/cancel_/other writes are blocked on this machine" if config.DRY_RUN else "OFF — orders you place are real"
    return base.rstrip() + f"\n\nDRY_RUN is {mode}.\nModel: {config.OLLAMA_MODEL}.\n"


def check_ollama() -> tuple[bool, str]:
    try:
        response = httpx.get(f"{config.OLLAMA_HOST}/api/tags", timeout=5)
        response.raise_for_status()
        names = [str(item.get("name", "")) for item in response.json().get("models", [])]
    except Exception as exc:
        return False, f"Ollama not reachable at {config.OLLAMA_HOST}: {exc}"
    if config.OLLAMA_MODEL in names:
        return True, "ok"
    if ":" not in config.OLLAMA_MODEL and any(name.split(":")[0] == config.OLLAMA_MODEL for name in names):
        return True, "ok"
    have = ", ".join(names) or "(none)"
    return False, (
        f"Model {config.OLLAMA_MODEL} is not pulled. Installed: {have}. "
        f"Run: ollama pull {config.OLLAMA_MODEL}"
    )


def _chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {
            "temperature": config.OLLAMA_TEMPERATURE,
            "num_ctx": config.OLLAMA_NUM_CTX,
        },
    }
    response = httpx.post(f"{config.OLLAMA_HOST}/api/chat", json=payload, timeout=600)
    if response.status_code >= 400:
        raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text[:800]}")
    body = response.json()
    message = body.get("message")
    if not isinstance(message, dict):
        raise RuntimeError(f"Ollama reply missing message: {str(body)[:500]}")
    return message


def _brief(arguments: dict[str, Any]) -> str:
    keys = ("symbol", "side", "type", "dollar_amount", "quantity", "account_number")
    picked = {key: arguments[key] for key in keys if key in arguments}
    return json.dumps(picked, default=str) if picked else ""


class Agent:
    def __init__(
        self,
        mcp: RobinhoodMcp,
        *,
        tool_allowlist: tuple[str, ...] | None = None,
        idempotency_day: str | None = None,
        enable_report: bool = False,
    ):
        self.mcp = mcp
        self.tool_allowlist = tool_allowlist
        self.idempotency_day = idempotency_day
        self.enable_report = enable_report
        self.agentic_accounts: set[str] = set()
        self.known_tools: set[str] = set()
        self.report: dict[str, Any] | None = None
        self.trace: list[dict[str, Any]] = []

    def _select_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.tool_allowlist:
            return tools
        allowed = set(self.tool_allowlist)
        selected = [tool for tool in tools if tool.get("name") in allowed]
        missing = allowed - {tool.get("name") for tool in selected}
        if missing:
            print(f"[tools] not advertised by the server: {', '.join(sorted(missing))}", flush=True)
        return selected

    def _local_call(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "write_run_report":
            markdown = str(arguments.get("markdown") or "")
            data = arguments.get("data") or {}
            if not isinstance(data, dict):
                return "data must be an object"
            day = str(data.get("date") or date.today().isoformat())
            data.setdefault("date", day)
            data.setdefault("dry_run", config.DRY_RUN)
            config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
            json_path = config.RUNS_DIR / f"{day}-entry.json"
            md_path = config.RUNS_DIR / f"{day}-entry.md"
            json_path.write_text(json.dumps(data, indent=2) + "\n")
            md_path.write_text(markdown.rstrip() + "\n")
            self.report = {"json": str(json_path), "markdown": str(md_path), "data": data}
            return f"Wrote {md_path} and {json_path}"
        return f"Unknown local tool {name}"

    def _call(self, name: str, arguments: dict[str, Any]) -> str:
        arguments = _coerce_order_args(name, arguments)
        if (
            self.idempotency_day
            and name == "place_equity_order"
            and arguments.get("symbol")
            and arguments.get("side")
        ):
            arguments["ref_id"] = stable_ref_id(
                str(arguments["symbol"]),
                str(arguments["side"]),
                self.idempotency_day,
            )
        blocked = order_guard(name, arguments, self.agentic_accounts)
        if blocked:
            text = blocked
        elif name == "write_run_report":
            text = self._local_call(name, arguments)
        elif self.known_tools and name not in self.known_tools:
            text = f"Unknown tool {name}. Use only the tools from this run."
        else:
            result = self.mcp.call_tool(name, arguments)
            text = tool_result_text(result)
            if name == "get_accounts":
                found = extract_agentic_accounts(text)
                if found:
                    self.agentic_accounts = found
                    print(f"[accounts] agentic account(s): {', '.join(sorted(found))}", flush=True)
        if len(text) > 4000:
            text = text[:4000] + "\n…[truncated]"
        self.trace.append({
            "tool": name,
            "arguments": arguments,
            "result_preview": text[:1500],
        })
        preview = text.replace("\n", " ")[:180]
        print(f"[tool] {name} {_brief(arguments)} → {preview}", flush=True)
        return text

    def run(self, user_prompt: str) -> AgentResult:
        ok, detail = check_ollama()
        if not ok:
            raise RuntimeError(detail)
        print(f"Connecting to Robinhood MCP ({'DRY_RUN' if self.mcp.dry_run else 'LIVE'})…", flush=True)
        self.mcp.initialize()
        remote_tools = self._select_tools(self.mcp.list_tools())
        extra = LOCAL_TOOLS if self.enable_report else []
        self.known_tools = {str(tool["name"]) for tool in remote_tools}
        if self.enable_report:
            self.known_tools.add("write_run_report")
        tools = ollama_tools(remote_tools, extra)
        print(f"Tools in this run: {len(tools)}", flush=True)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": user_prompt},
        ]
        final = ""
        for round_index in range(1, config.MAX_TOOL_ROUNDS + 1):
            message = _chat(messages, tools)
            messages.append(message)
            calls = extract_tool_calls(message)
            if not calls:
                final = strip_think(message.get("content") or "")
                print(f"\n{final}", flush=True)
                break
            print(f"[round {round_index}] {len(calls)} tool call(s)", flush=True)
            for name, arguments in calls:
                messages.append({
                    "role": "tool",
                    "tool_name": name,
                    "content": self._call(name, arguments),
                })
        else:
            final = "Stopped: reached MAX_TOOL_ROUNDS before the model finished."
            print(final, flush=True)
        return AgentResult(text=final, trace=self.trace, report=self.report)
