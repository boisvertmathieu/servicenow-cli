"""Interactive SSO login through a real browser window (Playwright).

The user signs in exactly as they would in their normal browser (SAML / OIDC
redirect to the corporate IdP, MFA, etc.). Once the browser is back on the
instance with an authenticated session, the session cookies and the X-UserToken
are captured and the window is closed.

A persistent browser profile is kept per sncli profile so the IdP's own "keep me
signed in" cookies survive; this lets ``silent_relogin`` renew an expired
ServiceNow session headlessly, without user interaction, while the IdP session
is still valid.

On Ubuntu WSL the window is shown through WSLg (Windows 11 / Windows 10 with
WSLg). Without WSLg, use ``sn login --method oauth`` or ``--method cookie``.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable
from urllib.parse import urlparse

from ..config import state_dir
from .session import fetch_user_token, looks_like_session, make_session_creds, validate_session

LOGIN_PATH_MARKERS = ("login.do", "login_with_sso.do", "external_logout_complete.do",
                      "logout_success.do", "validate_multifactor_auth_code.do", "saml_redirector.do")


class BrowserLoginError(RuntimeError):
    pass


def _browser_profile_dir(profile: str) -> str:
    d = state_dir() / "browser-profile" / profile
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return str(d)


def _launch(pw, profile: str, headless: bool):
    kwargs: dict[str, Any] = {"headless": headless, "viewport": None if not headless else {"width": 1280, "height": 900}}
    exe = os.environ.get("SN_BROWSER_PATH")
    channel = os.environ.get("SN_BROWSER_CHANNEL")  # e.g. "chrome" or "msedge" if installed in WSL
    if exe:
        kwargs["executable_path"] = exe
    elif channel:
        kwargs["channel"] = channel
    if os.environ.get("SN_BROWSER_NO_SANDBOX"):
        kwargs["args"] = ["--no-sandbox"]
    try:
        return pw.chromium.launch_persistent_context(_browser_profile_dir(profile), **kwargs)
    except Exception as e:  # pragma: no cover - environment specific
        raise BrowserLoginError(
            f"Could not start the browser: {e}\n"
            'Install it with:  "$(uv tool dir)/servicenow-cli/bin/playwright" install --with-deps chromium\n'
            "(from a source checkout: uv run playwright install --with-deps chromium)\n"
            "On WSL, a visible window needs WSLg; otherwise use `sn login --method oauth` or `--method cookie`."
        ) from e


def _on_instance(url: str, host: str) -> bool:
    u = urlparse(url)
    return u.hostname == host and not any(m in u.path for m in LOGIN_PATH_MARKERS)


def _page_token(context) -> str | None:
    for page in context.pages:
        for frame in page.frames:
            try:
                tok = frame.evaluate("() => (typeof window.g_ck === 'string' && window.g_ck) || null")
            except Exception:
                tok = None
            if tok:
                return tok
    return None


def _instance_cookies(context, host: str) -> list[dict[str, Any]]:
    out = []
    for c in context.cookies():
        dom = (c.get("domain") or "").lstrip(".")
        if host == dom or host.endswith("." + dom):
            out.append({k: c.get(k) for k in ("name", "value", "domain", "path", "expires", "secure", "httpOnly")})
    return out


def _capture(context, instance: str, verify: bool | str) -> dict[str, Any] | None:
    host = urlparse(instance).hostname or ""
    if not any(_on_instance(p.url, host) for p in context.pages):
        return None
    cookies = _instance_cookies(context, host)
    if not looks_like_session(cookies):
        return None
    token = _page_token(context)
    if not token:
        token, cookies = fetch_user_token(instance, cookies, verify=verify)
    user = validate_session(instance, cookies, token or "", verify=verify)
    if not user:  # still a guest session, or the IdP round-trip has not finished
        return None
    creds = make_session_creds(instance, cookies, token or "", source="browser")
    creds["user"] = user
    return creds


def browser_login(instance: str, profile: str, *, login_path: str | None = None, headless: bool = False,
                  timeout: float = 300.0, verify: bool | str = True,
                  status: Callable[[str], None] = lambda s: None,
                  _page_hook: Callable[[Any], None] | None = None) -> dict[str, Any]:
    """Open the instance, wait for the user to finish SSO, return session credentials."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserLoginError(
            "Browser login needs Playwright. Reinstall with the 'browser' extra:\n"
            '  uv tool install --force "servicenow-cli[browser] @ git+https://github.com/boisvertmathieu/servicenow-cli.git"\n'
            '  "$(uv tool dir)/servicenow-cli/bin/playwright" install --with-deps chromium'
        ) from e

    start_url = f"{instance}/{(login_path or '').lstrip('/')}"
    with sync_playwright() as pw:
        ctx = _launch(pw, profile, headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(start_url, wait_until="domcontentloaded")
            if _page_hook:
                _page_hook(page)
            status("Complete the sign-in in the browser window (SSO / MFA)...")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not ctx.pages:
                    raise BrowserLoginError("The browser window was closed before sign-in completed.")
                try:
                    creds = _capture(ctx, instance, verify)
                except BrowserLoginError:
                    raise
                except Exception:
                    creds = None
                if creds:
                    return creds
                ctx.pages[0].wait_for_timeout(1000)
            raise BrowserLoginError(f"Timed out after {int(timeout)}s waiting for sign-in.")
        finally:
            ctx.close()


def silent_relogin(instance: str, profile: str, *, login_path: str | None = None,
                   verify: bool | str = True, timeout: float = 25.0) -> dict[str, Any] | None:
    """Try to renew the session headlessly using the IdP cookies in the saved profile."""
    try:
        return browser_login(instance, profile, login_path=login_path, headless=True,
                             timeout=timeout, verify=verify)
    except Exception:
        return None
