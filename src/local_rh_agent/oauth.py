"""OAuth 2.1 + PKCE for Robinhood Trading MCP (public client, no secret)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

from . import config


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _load() -> dict[str, Any] | None:
    if not config.TOKEN_PATH.exists():
        return None
    return json.loads(config.TOKEN_PATH.read_text())


def _save(data: dict[str, Any]) -> None:
    config.TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    config.TOKEN_PATH.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(config.TOKEN_PATH, 0o600)
    os.chmod(config.TOKENS_DIR, 0o700)


def discover_oauth() -> dict[str, str]:
    """Read Robinhood's published OAuth metadata. Fall back to known endpoints."""
    defaults = {
        "authorization_endpoint": config.OAUTH_AUTHZ,
        "token_endpoint": config.OAUTH_TOKEN,
        "registration_endpoint": config.OAUTH_REGISTER,
    }
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            protected = client.get(
                "https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading"
            )
            meta_urls = [
                "https://agent.robinhood.com/.well-known/oauth-authorization-server",
            ]
            if protected.status_code == 200:
                servers = protected.json().get("authorization_servers") or []
                for issuer in servers:
                    meta_urls.insert(
                        0,
                        issuer.rstrip("/") + "/.well-known/oauth-authorization-server",
                    )
            data: dict[str, Any] | None = None
            for url in meta_urls:
                response = client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    break
            if not data:
                raise RuntimeError(f"no metadata ({protected.status_code})")
    except Exception as exc:
        print(f"OAuth discovery failed ({exc}); using built-in endpoints.")
        return defaults

    found = dict(defaults)
    for key in defaults:
        if data.get(key):
            found[key] = data[key]
    return found


def register_client(register_url: str) -> dict[str, Any]:
    payload = {
        "client_name": config.CLIENT_NAME,
        "redirect_uris": [config.REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "internal",
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(register_url, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Client registration failed HTTP {response.status_code}: {response.text[:500]}"
            )
        body = response.json()
    if "client_id" not in body:
        raise RuntimeError(f"Registration response missing client_id: {body}")
    return body


def login() -> dict[str, Any]:
    """Browser PKCE login. Blocks until the local callback. Saves tokens."""
    endpoints = discover_oauth()
    registration = register_client(endpoints["registration_endpoint"])
    client_id = registration["client_id"]
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)

    result: dict[str, str] = {}
    event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(parsed.query)
            if query.get("state", [None])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"bad state")
                return
            if "error" in query:
                result["error"] = query["error"][0]
                description = query.get("error_description", [""])[0]
                if description:
                    result["error_description"] = description
            else:
                result["code"] = query.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Robinhood auth complete.</h1>"
                b"<p>You can close this tab and go back to the terminal.</p></body></html>"
            )
            event.set()

        def log_message(self, *_args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", config.OAUTH_PORT), Handler)
    server.timeout = 1

    def _serve() -> None:
        while not event.is_set():
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": config.REDIRECT_URI,
        "scope": "internal",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": config.OAUTH_RESOURCE,
    }
    url = f"{endpoints['authorization_endpoint']}?{urllib.parse.urlencode(params)}"
    print("Opening browser for Robinhood OAuth.")
    print("If it does not open, paste this URL yourself:")
    print(url)
    webbrowser.open(url)

    if not event.wait(timeout=300):
        server.server_close()
        raise TimeoutError("OAuth callback timed out after 5 minutes")
    server.server_close()

    if "error" in result:
        detail = result.get("error_description") or result["error"]
        raise RuntimeError(f"OAuth error: {detail}")
    code = result.get("code")
    if not code:
        raise RuntimeError("No authorization code returned")

    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.REDIRECT_URI,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": config.OAUTH_RESOURCE,
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(endpoints["token_endpoint"], data=form)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Token exchange failed HTTP {response.status_code}: {response.text[:500]}"
            )
        tokens = response.json()

    bundle = {
        "client_id": client_id,
        "token_endpoint": endpoints["token_endpoint"],
        "obtained_at": int(time.time()),
        **tokens,
    }
    _save(bundle)
    print(f"Saved tokens → {config.TOKEN_PATH}")
    return bundle


def _refresh(bundle: dict[str, Any]) -> str:
    refresh = bundle.get("refresh_token")
    if not refresh:
        raise RuntimeError("Access token expired and no refresh_token. Re-run auth.")
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": bundle["client_id"],
        "resource": config.OAUTH_RESOURCE,
    }
    token_url = bundle.get("token_endpoint") or config.OAUTH_TOKEN
    with httpx.Client(timeout=30) as client:
        response = client.post(token_url, data=form)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Token refresh failed HTTP {response.status_code}: {response.text[:500]}. "
                "Re-run: python -m local_rh_agent auth"
            )
        tokens = response.json()
    bundle.update(tokens)
    bundle["obtained_at"] = int(time.time())
    _save(bundle)
    access = bundle.get("access_token")
    if not access:
        raise RuntimeError("Refresh response had no access_token")
    return access


def refresh_if_needed() -> str:
    """Return a valid access token, refreshing when close to expiry."""
    bundle = _load()
    if not bundle:
        raise RuntimeError("Not authenticated. Run: python -m local_rh_agent auth")

    expires_in = int(bundle.get("expires_in", 3600))
    obtained = int(bundle.get("obtained_at", 0))
    access = bundle.get("access_token")
    if access and time.time() < obtained + expires_in - 120:
        return access
    return _refresh(bundle)


def force_refresh() -> str:
    bundle = _load()
    if not bundle:
        raise RuntimeError("Not authenticated. Run: python -m local_rh_agent auth")
    return _refresh(bundle)


def token_status() -> str:
    bundle = _load()
    if not bundle:
        return "missing"
    expires_in = int(bundle.get("expires_in", 3600))
    obtained = int(bundle.get("obtained_at", 0))
    remaining = int(obtained + expires_in - time.time())
    if remaining <= 0:
        if bundle.get("refresh_token"):
            return "expired (refresh token present)"
        return "expired"
    return f"valid for ~{remaining // 60} min"
