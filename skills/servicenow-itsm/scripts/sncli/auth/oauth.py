"""OAuth 2.0 authorization-code + PKCE against a ServiceNow instance.

The SSO happens in the browser on ``/oauth_auth.do`` (the instance redirects to
the corporate IdP as usual), so no password ever touches the CLI.

Two redirect styles are supported:

* Loopback (default): ``http://localhost:<port>/callback``. The CLI listens on
  that port and receives the code automatically. WSL2 forwards ``localhost``
  from Windows into the distro by default, so a Windows browser works.
* Out-of-band: a redirect URI that is a *path on the instance* (starts with
  ``/``). The instance shows the code and the user pastes it into the terminal.
  This is how ServiceNow's own SDK CLI does it (redirect ``/sdk-oauth.do``,
  public client, PKCE S256, ``token_endpoint_auth_method: none``) and it needs
  no local listener at all.

Requires an OAuth client on the instance (System OAuth > Application Registry,
"Create an OAuth API endpoint for external clients", public client, with the
redirect URL registered). See README for the admin checklist.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..config import OAuthSettings
from ..wsl import open_url

# Values observed in @servicenow/sdk-cli 4.14.2 dist/auth/OAuth/CodeGrant.js (see docs/).
SDK_PUBLIC_CLIENT_ID = "543e5655f77746a28228c6009a599dfb"
SDK_REDIRECT_PATH = "/sdk-oauth.do"


class OAuthError(RuntimeError):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorization_url(instance: str, s: OAuthSettings, state: str, challenge: str) -> str:
    q = {
        "response_type": "code",
        "client_id": s.client_id,
        "redirect_uri": s.redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if s.scope:
        q["scope"] = s.scope
    return f"{instance}/oauth_auth.do?{urlencode(q)}"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    expected_path = "/callback"

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path != self.expected_path:
            self.send_error(404)
            return
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        type(self).result.update(params)
        ok = "code" in params
        body = (
            "<h2>Signed in. You can close this tab and return to the terminal.</h2>" if ok
            else f"<h2>Sign-in failed: {params.get('error', 'unknown')}</h2>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def _wait_for_loopback(redirect_uri: str, timeout: float) -> dict[str, str]:
    u = urlparse(redirect_uri)
    if u.hostname not in ("localhost", "127.0.0.1"):
        raise OAuthError("Loopback redirect must use localhost or 127.0.0.1")
    handler = type("H", (_CallbackHandler,), {"result": {}, "expected_path": u.path or "/"})
    srv = http.server.HTTPServer(("127.0.0.1", u.port or 80), handler)
    srv.timeout = 1
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and not handler.result:
            srv.handle_request()
    finally:
        srv.server_close()
    if not handler.result:
        raise OAuthError(f"Timed out after {int(timeout)}s waiting for the OAuth redirect.")
    return handler.result


def _token_request(instance: str, data: dict[str, str], s: OAuthSettings, verify: bool | str) -> dict[str, Any]:
    data = {**data, "client_id": s.client_id or ""}
    if s.client_secret:
        data["client_secret"] = s.client_secret
    with httpx.Client(verify=verify, timeout=30) as c:
        r = c.post(f"{instance}/oauth_token.do", data=data, headers={"Accept": "application/json"})
    try:
        body = r.json()
    except ValueError:
        body = {"error": r.text[:200]}
    if r.status_code != 200 or "access_token" not in body:
        raise OAuthError(f"Token endpoint returned {r.status_code}: {body.get('error_description') or body.get('error') or body}")
    return body


def _to_creds(instance: str, s: OAuthSettings, tok: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "oauth",
        "instance": instance,
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token"),
        "expires_at": int(time.time()) + int(tok.get("expires_in", 1800)),
        "client_id": s.client_id,
        "client_secret": s.client_secret,
        "redirect_uri": s.redirect_uri,
        "scope": tok.get("scope", s.scope),
        "obtained_at": int(time.time()),
    }


def oauth_login(instance: str, s: OAuthSettings, *, verify: bool | str = True, timeout: float = 300.0,
                status: Callable[[str], None] = print, prompt_code: Callable[[], str] | None = None,
                opener: Callable[[str], bool] = open_url) -> dict[str, Any]:
    if not s.client_id:
        raise OAuthError("No OAuth client_id configured. Run `sn configure --oauth-client-id ...` "
                         "or use `sn login --method oauth --sdk-client`.")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    url = authorization_url(instance, s, state, challenge)
    oob = s.redirect_uri.startswith("/")
    status("Opening your browser to sign in (SSO). If it does not open, visit:\n  " + url)
    result: dict[str, str] = {}
    if oob:
        opener(url)
        if prompt_code is None:
            raise OAuthError("Out-of-band redirect needs an interactive prompt.")
        result = {"code": prompt_code().strip(), "state": state}
    else:
        box: dict[str, Any] = {}

        def run():
            try:
                box["r"] = _wait_for_loopback(s.redirect_uri, timeout)
            except Exception as e:  # pragma: no cover - surfaced below
                box["e"] = e

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.2)  # listener up before the browser can redirect
        opener(url)
        t.join(timeout + 5)
        if "e" in box:
            raise box["e"]
        result = box.get("r", {})
    if "error" in result:
        raise OAuthError(f"Authorization failed: {result.get('error')} {result.get('error_description', '')}".strip())
    if result.get("state") != state:
        raise OAuthError("OAuth state mismatch; aborting (possible CSRF).")
    if not result.get("code"):
        raise OAuthError("No authorization code received.")
    tok = _token_request(instance, {
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": s.redirect_uri,
        "code_verifier": verifier,
    }, s, verify)
    return _to_creds(instance, s, tok)


def refresh(creds: dict[str, Any], *, verify: bool | str = True) -> dict[str, Any]:
    if not creds.get("refresh_token"):
        raise OAuthError("No refresh token; run `sn login` again.")
    s = OAuthSettings(client_id=creds.get("client_id"), client_secret=creds.get("client_secret"),
                      redirect_uri=creds.get("redirect_uri") or "", scope=creds.get("scope") or "")
    tok = _token_request(creds["instance"], {"grant_type": "refresh_token",
                                             "refresh_token": creds["refresh_token"]}, s, verify)
    new = _to_creds(creds["instance"], s, tok)
    if not new.get("refresh_token"):
        new["refresh_token"] = creds["refresh_token"]
    return new
