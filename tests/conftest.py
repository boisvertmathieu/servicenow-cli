import os
import re

import httpx
import pytest

from fake_servicenow import PASSWORD, FakeServiceNow


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SNCLI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SNCLI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SNCLI_NO_KEYRING", "1")
    # Local fake servers must never go through an outbound proxy.
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    for k in list(os.environ):
        if k.startswith("SN_") and k not in ("SN_BROWSER_PATH",):
            monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def fake():
    with FakeServiceNow() as f:
        yield f


def sso_browser_session(fake) -> httpx.Client:
    """Simulate a user's browser signing in via the IdP; returns the client holding cookies."""
    c = httpx.Client(follow_redirects=True, trust_env=False)
    r = c.get(f"{fake.url}/navpage.do")
    relay = re.search(r'name=RelayState type=hidden value="([^"]*)"', r.text).group(1)
    r = c.post(f"{fake.state.idp_url}/idp/login",
               data={"username": "jdoe", "password": PASSWORD, "RelayState": relay})
    assert "Welcome" in r.text
    return c


@pytest.fixture
def browser_session(fake):
    c = sso_browser_session(fake)
    yield c
    c.close()


@pytest.fixture
def unused_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
