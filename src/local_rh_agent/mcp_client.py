"""Streamable-HTTP MCP client for Robinhood Trading MCP."""
from __future__ import annotations

import json
from typing import Any

import httpx

from . import config
from .oauth import force_refresh, refresh_if_needed

MUTATING_PREFIXES = config.MUTATING_PREFIXES


class McpError(RuntimeError):
    pass


def tool_result_text(result: Any) -> str:
    """Flatten an MCP tools/call result into text for the model."""
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        parts: list[str] = []
        for block in result["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(json.dumps(block, default=str))
        text = "\n".join(parts).strip()
        if result.get("isError"):
            text = "TOOL ERROR:\n" + text
        return text or json.dumps(result, default=str)
    return json.dumps(result, default=str)


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    data_buf: list[str] = []

    def flush() -> None:
        if not data_buf:
            return
        raw = "\n".join(data_buf).strip()
        data_buf.clear()
        if not raw or raw == "[DONE]":
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            events.append(payload)

    for line in text.splitlines():
        if line.startswith("data:"):
            data_buf.append(line[5:].lstrip())
        elif line.strip() == "":
            flush()
    flush()
    return events


class RobinhoodMcp:
    def __init__(self, url: str | None = None, dry_run: bool | None = None):
        self.url = url or config.RH_MCP_URL
        self.dry_run = config.DRY_RUN if dry_run is None else dry_run
        self.session_id: str | None = None
        self.protocol_version = "2025-06-18"
        self._id = 0
        self._client = httpx.Client(timeout=90.0)
        self._access_token: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RobinhoodMcp:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _token(self, force: bool = False) -> str:
        if force or not self._access_token:
            self._access_token = force_refresh() if force else refresh_if_needed()
        return self._access_token

    def _headers(self, force_token: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token(force=force_token)}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _capture_session(self, response: httpx.Response) -> None:
        session_id = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id

    def _parse_body(self, response: httpx.Response, rpc_id: int | None) -> dict[str, Any]:
        self._capture_session(response)
        content_type = response.headers.get("content-type", "")
        text = response.text
        if not text.strip():
            return {}
        if "text/event-stream" in content_type:
            events = _parse_sse(text)
            if not events:
                raise McpError(f"Empty SSE body: {text[:500]}")
            if rpc_id is not None:
                for event in events:
                    if event.get("id") == rpc_id:
                        payload = event
                        break
                else:
                    payload = events[-1]
            else:
                payload = events[-1]
        else:
            payload = response.json()
        if isinstance(payload, dict) and "error" in payload:
            raise McpError(json.dumps(payload["error"]))
        return payload if isinstance(payload, dict) else {"result": payload}

    def _post(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        notification: bool,
    ) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        rpc_id: int | None = None
        if not notification:
            rpc_id = self._next_id()
            body["id"] = rpc_id
        if params is not None:
            body["params"] = params

        response: httpx.Response | None = None
        for attempt in range(2):
            response = self._client.post(
                self.url,
                headers=self._headers(force_token=attempt > 0),
                json=body,
            )
            if response.status_code == 401 and attempt == 0:
                self._access_token = None
                continue
            break
        assert response is not None
        if notification:
            self._capture_session(response)
            if response.status_code >= 400 and response.status_code not in {202, 204}:
                raise McpError(f"HTTP {response.status_code}: {response.text[:800]}")
            return None
        if response.status_code >= 400:
            raise McpError(f"HTTP {response.status_code}: {response.text[:800]}")
        payload = self._parse_body(response, rpc_id)
        return payload.get("result")

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return self._post(method, params, notification=False)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._post(method, params, notification=True)

    def initialize(self) -> dict[str, Any]:
        versions = ("2025-06-18", "2025-03-26", "2024-11-05")
        last_error: Exception | None = None
        for version in versions:
            self.protocol_version = version
            self.session_id = None
            try:
                result = self.request(
                    "initialize",
                    {
                        "protocolVersion": version,
                        "capabilities": {},
                        "clientInfo": {"name": "local-rh-agent", "version": "0.1.0"},
                    },
                )
            except McpError as exc:
                last_error = exc
                continue
            if isinstance(result, dict) and result.get("protocolVersion"):
                self.protocol_version = str(result["protocolVersion"])
            self.notify("notifications/initialized")
            return result or {}
        raise McpError(f"initialize failed: {last_error}")

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = self.request("tools/list", params or None) or {}
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor") or None
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if self.dry_run and name.startswith(MUTATING_PREFIXES):
            return {
                "dry_run": True,
                "blocked": True,
                "tool": name,
                "arguments": arguments or {},
                "message": "DRY_RUN=1 — mutating tool was not sent to Robinhood",
            }
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})
