"""End-to-end tests of the CLI against the fake instance (cookie + OAuth flows)."""

import json
import re
import threading

import httpx
from click.testing import CliRunner

from sncli import store
from sncli.auth import oauth
from sncli.cli import main
from sncli.client import ServiceNowClient
from sncli.config import Config, OAuthSettings


def run(*args, input=None):
    res = CliRunner().invoke(main, list(args), input=input, catch_exceptions=False)
    return res


def cookie_header(c: httpx.Client, host="localhost") -> str:
    return "; ".join(f"{ck.name}={ck.value}" for ck in c.cookies.jar if ck.domain.lstrip(".").startswith(host))


def test_cookie_login_search_get_query_kb(fake, browser_session):
    assert run("configure", "--instance", fake.url).exit_code == 0
    res = run("login", "--method", "cookie", "--cookie", cookie_header(browser_session))
    assert res.exit_code == 0, res.output
    assert "Jane Doe" in res.output

    res = run("whoami", "--format", "json")
    assert json.loads(res.output)["user_name"] == "jdoe"

    res = run("search", "vpn", "--format", "json")
    by_table = {r["table"]: r for r in json.loads(res.output)}
    assert {r["number"] for r in by_table["incident"]["rows"]} == {"INC0010001", "INC0010003"}
    assert by_table["kb_knowledge"]["rows"][0]["number"] == "KB0001001"
    assert by_table["incident"]["rows"][0]["_url"].startswith(fake.url + "/nav_to.do?uri=incident.do")

    # keyword search not available on this table -> automatic LIKE fallback
    res = run("search", "vpn", "-t", "u_not_indexed", "--format", "json")
    r = json.loads(res.output)[0]
    assert r["mode"] == "like" and r["rows"][0]["number"] == "U0001"

    res = run("search", "vpn", "-t", "incident", "-q", "priority=1 - Critical", "--format", "csv")
    assert "INC0010001" in res.output and "INC0010003" not in res.output

    res = run("get", "INC0010002", "--format", "json")
    assert json.loads(res.output)["short_description"] == "Printer jam floor 3"
    res = run("get", "incident", "c" * 32, "--format", "json")
    assert json.loads(res.output)["number"] == "INC0010003"

    res = run("query", "incident", "-q", "assignment_group=Network", "-f", "number", "--format", "json")
    assert [r["number"] for r in json.loads(res.output)] == ["INC0010001", "INC0010003"]

    res = run("kb", "vpn", "client", "--format", "json")
    rows = json.loads(res.output)
    assert rows[0]["number"] == "KB0001001" and rows[0]["snippet"].startswith("Install")

    res = run("api", "/api/now/table/incident", "-p", "sysparm_limit=1")
    assert len(json.loads(res.output)["result"]) == 1

    res = run("search", "vpn")  # human table output
    assert "INC0010001" in res.output

    assert json.loads(run("status").output)["auth"] == "session (cookie)"
    assert run("logout").exit_code == 0
    res = CliRunner().invoke(main, ["whoami"])
    assert res.exit_code != 0 and "sn login" in res.output


def test_guest_cookie_is_rejected(fake):
    guest = httpx.Client(trust_env=False)
    guest.post(f"{fake.url}/angular.do?sysparm_type=get_user")  # obtains a guest JSESSIONID
    run("configure", "--instance", fake.url)
    res = CliRunner().invoke(main, ["login", "--method", "cookie", "--cookie", cookie_header(guest)])
    assert res.exit_code != 0 and "rejected" in res.output
    assert store.load("default") is None


def test_expired_cookie_session_reports_relogin(fake, browser_session):
    run("configure", "--instance", fake.url)
    run("login", "--method", "cookie", "--cookie", cookie_header(browser_session))
    fake.state.expire_sessions()
    res = CliRunner().invoke(main, ["search", "vpn"])
    assert res.exit_code != 0 and "sn login" in res.output


def test_rotated_cookies_are_persisted(fake, browser_session):
    run("configure", "--instance", fake.url)
    run("login", "--method", "cookie", "--cookie", cookie_header(browser_session))
    run("search", "vpn")
    names = {c["name"] for c in store.load("default")["cookies"]}
    assert "glide_user_activity" in names  # Set-Cookie from API response absorbed


def _browser_opener(browser_session):
    """Return an opener that 'clicks' through the authorization URL with the SSO'd browser."""
    def opener(url):
        def go():
            browser_session.get(url, follow_redirects=True)
        threading.Thread(target=go, daemon=True).start()
        return True
    return opener


def test_oauth_loopback_pkce_and_refresh(fake, browser_session, unused_port):
    s = OAuthSettings(client_id="test-public-client", redirect_uri=f"http://localhost:{unused_port}/callback")
    creds = oauth.oauth_login(fake.url, s, timeout=20, status=lambda m: None,
                              opener=_browser_opener(browser_session))
    assert creds["type"] == "oauth" and creds["refresh_token"]
    store.save("default", creds)
    cfg = Config.load()
    p = cfg.profile()
    p.instance = fake.url
    with ServiceNowClient(p) as cl:
        assert cl.whoami()["user_name"] == "jdoe"
        old = cl.creds["access_token"]
        fake.state.expire_access_tokens()  # server-side revocation -> 401 -> refresh -> retry
        assert cl.whoami()["user_name"] == "jdoe"
        assert cl.creds["access_token"] != old
    assert store.load("default")["access_token"] != old  # persisted


def test_oauth_out_of_band_like_sdk(fake, browser_session):
    seen = {}

    def opener(url):
        seen["html"] = browser_session.get(url, follow_redirects=True).text
        return True

    s = OAuthSettings(client_id="test-public-client", redirect_uri="/sdk-oauth.do")
    creds = oauth.oauth_login(fake.url, s, timeout=20, status=lambda m: None, opener=opener,
                              prompt_code=lambda: re.search(r"id=code>([0-9a-f]+)<", seen["html"]).group(1))
    assert creds["access_token"]


def test_oauth_rejects_wrong_verifier(fake, browser_session, unused_port, monkeypatch):
    real = oauth.pkce_pair
    monkeypatch.setattr(oauth, "pkce_pair", lambda: ("tampered-verifier-" + "x" * 40, real()[1]))
    s = OAuthSettings(client_id="test-public-client", redirect_uri=f"http://localhost:{unused_port}/callback")
    try:
        oauth.oauth_login(fake.url, s, timeout=20, status=lambda m: None, opener=_browser_opener(browser_session))
    except oauth.OAuthError as e:
        assert "PKCE" in str(e)
    else:
        raise AssertionError("expected PKCE failure")


def test_env_bearer_token(fake, monkeypatch, browser_session, unused_port):
    s = OAuthSettings(client_id="test-public-client", redirect_uri=f"http://localhost:{unused_port}/callback")
    creds = oauth.oauth_login(fake.url, s, timeout=20, status=lambda m: None,
                              opener=_browser_opener(browser_session))
    monkeypatch.setenv("SN_INSTANCE", fake.url)
    monkeypatch.setenv("SN_BEARER_TOKEN", creds["access_token"])
    res = run("whoami", "--format", "json")
    assert json.loads(res.output)["user_name"] == "jdoe"
