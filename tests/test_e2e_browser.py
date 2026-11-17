"""Real-browser SSO login against the fake instance + IdP (Playwright, headless)."""

import json
import os

import pytest
from click.testing import CliRunner

from fake_servicenow import PASSWORD
from sncli import store
from sncli.cli import main
from sncli.client import ServiceNowClient
from sncli.config import Config

playwright = pytest.importorskip("playwright.sync_api")


def _browser_available() -> bool:
    try:
        with playwright.sync_playwright() as pw:
            kw = {"executable_path": os.environ["SN_BROWSER_PATH"]} if os.environ.get("SN_BROWSER_PATH") else {}
            pw.chromium.launch(**kw).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _browser_available(), reason="no Chromium available for Playwright")


def fill_idp_form(page):
    """Stands in for the human typing into the corporate IdP page."""
    page.wait_for_selector("#username")
    page.fill("#username", "jdoe")
    page.fill("#password", PASSWORD)
    page.click("#submit")


def test_browser_sso_login_then_silent_renewal(fake):
    from sncli.auth.browser import browser_login

    CliRunner().invoke(main, ["configure", "--instance", fake.url], catch_exceptions=False)
    creds = browser_login(fake.url, "default", headless=True, timeout=30, _page_hook=fill_idp_form)
    assert creds["type"] == "session" and creds["source"] == "browser"
    assert creds["user"]["user_name"] == "jdoe"
    assert creds["user_token"]  # captured from window.g_ck
    assert {c["name"] for c in creds["cookies"]} >= {"JSESSIONID", "glide_session_store"}
    assert all(c["domain"] == "localhost" for c in creds["cookies"])  # IdP cookies not leaked
    store.save("default", creds)

    res = CliRunner().invoke(main, ["search", "vpn", "-t", "incident", "--format", "json"], catch_exceptions=False)
    assert {r["number"] for r in json.loads(res.output)[0]["rows"]} == {"INC0010001", "INC0010003"}

    # ServiceNow session times out; the IdP session (cookie in the persistent browser
    # profile) is still valid -> the client renews headlessly, no user interaction.
    fake.state.expire_sessions()
    p = Config.load().profile()
    with ServiceNowClient(p) as cl:
        old_token = cl.creds["user_token"]
        assert cl.whoami()["user_name"] == "jdoe"
        assert cl.creds["user_token"] != old_token
    assert store.load("default")["user_token"] != old_token


def test_browser_login_times_out_without_user(fake):
    from sncli.auth.browser import BrowserLoginError, browser_login

    with pytest.raises(BrowserLoginError, match="Timed out"):
        browser_login(fake.url, "other", headless=True, timeout=3)
