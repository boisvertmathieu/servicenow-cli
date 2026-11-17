import base64
import hashlib

import pytest

from sncli.auth.oauth import pkce_pair
from sncli.auth.session import looks_like_session, parse_cookie_header
from sncli.config import normalize_instance
from sncli.search import build_text_query, fields_for, table_for_number


@pytest.mark.parametrize("raw,expected", [
    ("acme", "https://acme.service-now.com"),
    ("acme.service-now.com", "https://acme.service-now.com"),
    ("https://acme.service-now.com/", "https://acme.service-now.com"),
    ("https://servicenow.corp.example", "https://servicenow.corp.example"),
])
def test_normalize_instance(raw, expected):
    assert normalize_instance(raw) == expected


def test_parse_cookie_header():
    cookies = parse_cookie_header("Cookie: JSESSIONID=abc; glide_user_route=glide.x=y; BIGipServerpool=1", "h")
    assert [c["name"] for c in cookies] == ["JSESSIONID", "glide_user_route", "BIGipServerpool"]
    assert cookies[1]["value"] == "glide.x=y"
    assert looks_like_session(cookies)
    assert not looks_like_session(parse_cookie_header("foo=bar", "h"))


def test_pkce_pair_is_s256():
    v, c = pkce_pair()
    assert 43 <= len(v) <= 128
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def test_text_query_building():
    assert build_text_query("vpn down") == "123TEXTQUERY321=vpn down"
    assert build_text_query("a^b", "active=true") == "123TEXTQUERY321=a b^active=true"
    assert build_text_query("vpn", like=True) == "short_descriptionLIKEvpn^ORdescriptionLIKEvpn"


def test_number_prefixes_and_fields():
    assert table_for_number("inc0012345") == "incident"
    assert table_for_number("RITM0001") == "sc_req_item"
    assert table_for_number("SCTASK001") == "sc_task"
    assert table_for_number("hello") is None
    assert fields_for("incident")[-1] == "sys_id"
    assert fields_for("x", ["a"]) == ["a", "sys_id"]
