"""UI-session (cookie) authentication helpers.

Behaviour reverse engineered from ServiceNow's own SDK CLI (@servicenow/sdk-cli,
dist/auth/basic-auth/UISession.js and @servicenow/sdk-api dist/credentials.js):

* A browser session is a set of cookies (JSESSIONID, glide_session_store,
  glide_user_route, BIGipServer*, ...). REST calls made with those cookies must
  also carry the session's CSRF token in an ``X-UserToken`` header.
* The token can be obtained without scraping HTML by POSTing to
  ``/angular.do?sysparm_type=get_user`` with the cookies: the instance answers
  (usually with a 401) and returns the token in ``X-UserToken-Response``.
* The classic UI also exposes the same token as ``window.g_ck``.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx

SESSION_COOKIE_HINTS = ("JSESSIONID", "glide_session_store", "glide_user_route", "glide_user_session")


def cookie_header(cookies: list[dict[str, Any]]) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def parse_cookie_header(header: str, host: str) -> list[dict[str, Any]]:
    """Parse a pasted ``Cookie:`` header (or raw ``a=b; c=d``) into cookie dicts."""
    header = header.strip()
    if header.lower().startswith("cookie:"):
        header = header.split(":", 1)[1]
    out = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if name:
            out.append({"name": name, "value": value, "domain": host, "path": "/"})
    return out


def looks_like_session(cookies: list[dict[str, Any]]) -> bool:
    names = {c["name"] for c in cookies}
    return any(h in names for h in SESSION_COOKIE_HINTS)


def fetch_user_token(instance: str, cookies: list[dict[str, Any]], *, verify: bool | str = True,
                     timeout: float = 20.0) -> tuple[str, list[dict[str, Any]]]:
    """Return (user_token, cookies-with-any-updates) using the angular.do trick."""
    host = urlparse(instance).hostname or ""
    jar = {c["name"]: c for c in cookies}
    with httpx.Client(verify=verify, timeout=timeout, follow_redirects=False) as c:
        r = c.post(
            f"{instance}/angular.do",
            params={"sysparm_type": "get_user"},
            headers={"Cookie": cookie_header(list(jar.values())), "Accept": "application/json"},
        )
    for name, value in r.cookies.items():
        jar[name] = {"name": name, "value": value, "domain": host, "path": "/"}
    # Empty string is legitimate for the legacy (pre-token) security model.
    return r.headers.get("x-usertoken-response", ""), list(jar.values())


def validate_session(instance: str, cookies: list[dict[str, Any]], user_token: str, *,
                     verify: bool | str = True, timeout: float = 20.0) -> dict[str, Any] | None:
    """Return the signed-in user's record, or None if the session is guest/invalid."""
    headers = {"Cookie": cookie_header(cookies), "Accept": "application/json"}
    if user_token:
        headers["X-UserToken"] = user_token
    try:
        with httpx.Client(verify=verify, timeout=timeout, follow_redirects=False) as c:
            r = c.get(
                f"{instance}/api/now/table/sys_user",
                params={"sysparm_query": "sys_id=javascript:gs.getUserID()", "sysparm_limit": "1",
                        "sysparm_fields": "sys_id,user_name,name,email"},
                headers=headers,
            )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        rows = r.json().get("result") or []
    except ValueError:
        return None
    if not rows or rows[0].get("user_name") in (None, "", "guest"):
        return None
    return rows[0]


def make_session_creds(instance: str, cookies: list[dict[str, Any]], user_token: str,
                       source: str) -> dict[str, Any]:
    return {
        "type": "session",
        "instance": instance,
        "cookies": cookies,
        "user_token": user_token,
        "source": source,
        "obtained_at": int(time.time()),
    }
