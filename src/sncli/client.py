"""Authenticated HTTP client for the ServiceNow REST APIs."""

from __future__ import annotations

import os
import ssl
import sys
import threading
import time
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx

from . import __version__, store
from .auth import oauth
from .auth.session import cookie_header
from .config import Profile


class AuthRequired(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str, detail: str | None = None):
        super().__init__(f"HTTP {status}: {message}" + (f" ({detail})" if detail else ""))
        self.status, self.message, self.detail = status, message, detail


def tls_verify() -> bool | ssl.SSLContext:
    """Honour corporate CA bundles (common behind TLS-inspecting proxies)."""
    if os.environ.get("SN_INSECURE") == "1":
        return False
    bundle = os.environ.get("SN_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    return ssl.create_default_context(cafile=bundle) if bundle else True


def creds_from_env(instance: str | None) -> dict[str, Any] | None:
    """Pre-authenticated session from env vars (mirrors the SDK's SN_SDK_SESSION_* support)."""
    bearer, token, cookie = (os.environ.get(k) for k in ("SN_BEARER_TOKEN", "SN_USER_TOKEN", "SN_SESSION_COOKIE"))
    if not (bearer or token or cookie):
        return None
    if not instance:
        raise AuthRequired("SN_INSTANCE must be set when using SN_BEARER_TOKEN / SN_USER_TOKEN / SN_SESSION_COOKIE")
    if bearer:
        return {"type": "oauth", "instance": instance, "access_token": bearer, "expires_at": None, "ephemeral": True}
    from .auth.session import parse_cookie_header
    host = urlparse(instance).hostname or ""
    return {"type": "session", "instance": instance, "user_token": token or "",
            "cookies": parse_cookie_header(cookie or "", host), "ephemeral": True}


class ServiceNowClient:
    def __init__(self, profile: Profile, creds: dict[str, Any] | None = None, *, allow_relogin: bool = True,
                 timeout: float = 60.0):
        self.profile = profile
        self.verify = tls_verify()
        self.creds = creds or creds_from_env(profile.instance) or store.load(profile.name)
        if not self.creds:
            raise AuthRequired(f"Not signed in (profile '{profile.name}'). Run: sn login")
        self.instance = self.creds["instance"].rstrip("/")
        self.allow_relogin = allow_relogin
        self._lock = threading.Lock()
        self._dirty = False
        self.http = httpx.Client(
            base_url=self.instance, verify=self.verify, timeout=timeout, follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": f"sncli/{__version__}"},
        )

    # ---- auth plumbing -------------------------------------------------
    def _auth_headers(self) -> dict[str, str]:
        c = self.creds
        if c["type"] == "oauth":
            return {"Authorization": f"Bearer {c['access_token']}"}
        h = {"Cookie": cookie_header(c.get("cookies") or [])}
        if c.get("user_token"):
            h["X-UserToken"] = c["user_token"]
        return h

    def _maybe_refresh_oauth(self, force: bool = False) -> bool:
        c = self.creds
        if c["type"] != "oauth" or c.get("ephemeral"):
            return False
        if not force and c.get("expires_at") and c["expires_at"] - time.time() > 300:
            return False
        try:
            self.creds = oauth.refresh(c, verify=self.verify)
        except oauth.OAuthError:
            return False
        self._dirty = True
        return True

    def _renew_session(self) -> bool:
        c = self.creds
        if c["type"] != "session" or c.get("ephemeral") or not self.allow_relogin or c.get("source") != "browser":
            return False
        from .auth.browser import silent_relogin
        sys.stderr.write("ServiceNow session expired; renewing through SSO in the background...\n")
        new = silent_relogin(self.instance, self.profile.name, login_path=self.profile.sso_login_path,
                             verify=self.verify)
        if not new:
            return False
        self.creds, self._dirty = new, True
        return True

    def _absorb_cookies(self, r: httpx.Response) -> None:
        """Keep session cookies current (ServiceNow rotates some of them)."""
        if self.creds["type"] != "session" or not r.headers.get_list("set-cookie"):
            return
        host = urlparse(self.instance).hostname
        jar = {ck["name"]: ck for ck in self.creds.get("cookies") or []}
        for name, value in r.cookies.items():
            jar[name] = {**jar.get(name, {"domain": host, "path": "/"}), "name": name, "value": value}
        self.creds["cookies"] = list(jar.values())
        tok = r.headers.get("x-usertoken-response")
        if tok:
            self.creds["user_token"] = tok
        self._dirty = True

    def save(self) -> None:
        if self._dirty and not self.creds.get("ephemeral"):
            store.save(self.profile.name, self.creds)
            self._dirty = False

    def close(self) -> None:
        self.save()
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- requests ------------------------------------------------------
    def request(self, method: str, path: str, **kw) -> httpx.Response:
        with self._lock:
            self._maybe_refresh_oauth()
        r = self._send(method, path, **kw)
        if r.status_code == 401 or self._is_login_redirect(r, path):
            with self._lock:
                renewed = self._maybe_refresh_oauth(force=True) or self._renew_session()
            if renewed:
                r = self._send(method, path, **kw)
        if r.status_code == 401 or self._is_login_redirect(r, path):
            raise AuthRequired("Session expired or not authorised. Run: sn login")
        if r.status_code >= 400:
            msg, detail = r.reason_phrase, None
            try:
                err = r.json().get("error") or {}
                msg, detail = err.get("message") or msg, err.get("detail")
            except ValueError:
                pass
            raise ApiError(r.status_code, msg, detail)
        return r

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        headers = {**kw.pop("headers", {}), **self._auth_headers()}
        r = self.http.request(method, path, headers=headers, **kw)
        with self._lock:
            self._absorb_cookies(r)
        return r

    @staticmethod
    def _is_login_redirect(r: httpx.Response, path: str) -> bool:
        if not path.lstrip("/").startswith("api/"):
            return False
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location", "")
            return any(m in loc for m in ("login", "saml", "sso", "oauth_auth"))
        ctype = r.headers.get("content-type", "")
        return r.status_code == 200 and "text/html" in ctype and "login" in r.text[:4000].lower()

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params).json()

    # ---- Table API -----------------------------------------------------
    def table(self, table: str, *, query: str = "", fields: list[str] | None = None, limit: int = 20,
              offset: int = 0, display_value: str = "true", page_size: int = 100) -> Iterator[dict[str, Any]]:
        """Yield up to *limit* records from the Table API, paging transparently."""
        got = 0
        while got < limit:
            n = min(page_size, limit - got)
            params = {
                "sysparm_query": query, "sysparm_limit": n, "sysparm_offset": offset + got,
                "sysparm_display_value": display_value, "sysparm_exclude_reference_link": "true",
            }
            if fields:
                params["sysparm_fields"] = ",".join(fields)
            rows = self.get_json(f"/api/now/table/{table}", params).get("result") or []
            yield from rows
            got += len(rows)
            if len(rows) < n:
                break

    def record(self, table: str, sys_id: str, display_value: str = "true") -> dict[str, Any]:
        return self.get_json(f"/api/now/table/{table}/{sys_id}", {
            "sysparm_display_value": display_value, "sysparm_exclude_reference_link": "true"}).get("result") or {}

    def whoami(self) -> dict[str, Any]:
        rows = list(self.table("sys_user", query="sys_id=javascript:gs.getUserID()", limit=1,
                               fields=["sys_id", "user_name", "name", "email", "title", "department"]))
        if not rows or rows[0].get("user_name") in (None, "", "guest"):
            raise AuthRequired("Session is not authenticated (guest). Run: sn login")
        return rows[0]

    def record_url(self, table: str, sys_id: str) -> str:
        return f"{self.instance}/nav_to.do?uri={table}.do%3Fsys_id%3D{sys_id}"
