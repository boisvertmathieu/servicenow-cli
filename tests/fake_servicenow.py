"""A small fake ServiceNow instance + fake corporate IdP for tests.

Instance runs on http://localhost:<port>, IdP on http://127.0.0.1:<port> (different
hosts so cookies do not mix, like a real SSO round-trip).

Implements just enough real behaviour:
* every visitor gets a guest JSESSIONID; REST calls with a guest session -> 401
* unauthenticated UI hits redirect to the IdP; IdP posts back to /saml_consume
* authenticated pages expose window.g_ck; /angular.do?sysparm_type=get_user returns
  401 + X-UserToken-Response (as observed in @servicenow/sdk-cli UISession.js)
* cookie-authenticated REST calls require a matching X-UserToken
* Table API with 123TEXTQUERY321 keyword search, LIKE, number=, javascript:gs.getUserID()
* /oauth_auth.do + /oauth_token.do with PKCE S256, loopback and out-of-band redirects
* Knowledge API /api/sn_km_api/knowledge/articles
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlencode, urlparse

USER = {"sys_id": "6816f79cc0a8016401c5a33be04be441", "user_name": "jdoe", "name": "Jane Doe",
        "email": "jane.doe@example.com", "title": "Engineer", "department": "IT"}
PASSWORD = "correct horse"

DATA = {
    "incident": [
        {"sys_id": "a" * 32, "number": "INC0010001", "short_description": "VPN outage in Montreal office",
         "description": "Users cannot connect to the VPN", "state": "In Progress", "priority": "1 - Critical",
         "assigned_to": "Jane Doe", "assignment_group": "Network", "sys_updated_on": "2026-11-16 10:00:00"},
        {"sys_id": "b" * 32, "number": "INC0010002", "short_description": "Printer jam floor 3",
         "description": "Paper jam", "state": "New", "priority": "4 - Low", "assigned_to": "",
         "assignment_group": "Desktop", "sys_updated_on": "2026-11-15 09:00:00"},
        {"sys_id": "c" * 32, "number": "INC0010003", "short_description": "Slow VPN when on wifi",
         "description": "vpn latency", "state": "New", "priority": "3 - Moderate", "assigned_to": "",
         "assignment_group": "Network", "sys_updated_on": "2026-11-14 09:00:00"},
    ],
    "kb_knowledge": [
        {"sys_id": "d" * 32, "number": "KB0001001", "short_description": "How to configure the VPN client",
         "text": "Install the VPN client then ...", "workflow_state": "published",
         "kb_knowledge_base": "IT", "sys_updated_on": "2026-10-01 00:00:00"},
    ],
    "sc_req_item": [], "change_request": [], "problem": [],
    "u_not_indexed": [
        {"sys_id": "e" * 32, "number": "U0001", "short_description": "custom vpn record", "description": ""},
    ],
}
TEXT_INDEXED = {"incident", "kb_knowledge", "sc_req_item", "change_request", "problem", "task"}


def _s256(v: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


class State:
    def __init__(self):
        self.sessions: dict[str, dict] = {}   # JSESSIONID -> {"auth": bool, "token": str}
        self.idp_sessions: set[str] = set()
        self.codes: dict[str, dict] = {}
        self.access: dict[str, float] = {}
        self.refresh: set[str] = set()
        self.access_ttl = 1800
        self.oauth_clients = {"test-public-client": None}
        self.instance_url = ""
        self.idp_url = ""
        self.requests: list[tuple[str, str]] = []

    def expire_sessions(self):
        self.sessions.clear()

    def expire_access_tokens(self):
        self.access.clear()


def _match(row: dict, query: str, table: str) -> bool | str:
    """Evaluate a tiny subset of the encoded-query language."""
    if not query:
        return True
    terms = [t for t in query.split("^") if t and not t.startswith("ORDERBY")]
    groups: list[list[str]] = []
    for t in terms:
        if t.startswith("OR") and groups:
            groups[-1].append(t[2:])
        else:
            groups.append([t])
    for g in groups:
        ok = False
        for t in g:
            if t.startswith("123TEXTQUERY321="):
                if table not in TEXT_INDEXED:
                    return "not-indexed"
                words = t.split("=", 1)[1].lower().split()
                blob = " ".join(str(v) for v in row.values()).lower()
                ok = ok or all(w in blob for w in words)
            elif "LIKE" in t:
                f, v = t.split("LIKE", 1)
                ok = ok or v.lower() in str(row.get(f, "")).lower()
            elif "=" in t:
                f, v = t.split("=", 1)
                if v == "javascript:gs.getUserID()":
                    v = USER["sys_id"]
                ok = ok or str(row.get(f, "")) == v
        if not ok:
            return False
    return True


def make_handler(st: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        # ---------- helpers
        def _cookies(self) -> dict[str, str]:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return {k: m.value for k, m in c.items()}

        def _send(self, code: int, body: bytes | str = b"", ctype="application/json", headers=None, cookies=None):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            for k, v in (cookies or {}).items():
                self.send_header("Set-Cookie", f"{k}={v}; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj, **kw):
            self._send(code, json.dumps(obj), **kw)

        def _redirect(self, loc, cookies=None):
            self._send(302, b"", ctype="text/html", headers={"Location": loc}, cookies=cookies)

        def _session(self) -> tuple[str, dict, dict]:
            """Return (sid, session, set_cookies) creating a guest session if needed."""
            sid = self._cookies().get("JSESSIONID")
            set_c = {}
            if not sid or sid not in st.sessions:
                sid = secrets.token_hex(16)
                st.sessions[sid] = {"auth": False, "token": secrets.token_hex(20)}
                set_c["JSESSIONID"] = sid
            return sid, st.sessions[sid], set_c

        def _body(self) -> dict[str, str]:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode() if n else ""
            return {k: v[0] for k, v in parse_qs(raw).items()}

        @property
        def _is_idp(self) -> bool:
            return self.server.server_address[1] == urlparse(st.idp_url).port

        # ---------- routing
        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            st.requests.append(("GET", u.path))
            if self._is_idp:
                return self._idp_get(u.path, q)
            if u.path.startswith("/api/"):
                return self._api(u.path, q)
            if u.path == "/saml_consume":
                return self._saml_consume(q)
            if u.path == "/oauth_auth.do":
                return self._oauth_auth(q)
            # any UI page
            sid, sess, set_c = self._session()
            if not sess["auth"]:
                relay = st.instance_url + self.path
                return self._redirect(f"{st.idp_url}/idp/authorize?RelayState={quote(relay)}", set_c)
            html = (f"<html><head><title>ServiceNow</title><script>window.g_ck = '{sess['token']}';</script>"
                    f"</head><body><h1>Welcome {USER['name']}</h1></body></html>")
            self._send(200, html, ctype="text/html", cookies=set_c)

        def do_POST(self):  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            st.requests.append(("POST", u.path))
            if self._is_idp and u.path == "/idp/login":
                f = self._body()
                if f.get("username") == USER["user_name"] and f.get("password") == PASSWORD:
                    idp_sid = secrets.token_hex(16)
                    st.idp_sessions.add(idp_sid)
                    return self._redirect(f"{st.instance_url}/saml_consume?user=jdoe&RelayState={quote(f.get('RelayState', ''))}",
                                          {"IDP_SSO": f"{idp_sid}; Max-Age=3600"})  # "stay signed in" = persistent cookie
                return self._send(200, "<p id=err>bad credentials</p>", ctype="text/html")
            if u.path == "/angular.do" and q.get("sysparm_type") == "get_user":
                sid, sess, set_c = self._session()
                return self._json(401, {"error": "token"}, headers={"X-UserToken-Response": sess["token"]}, cookies=set_c)
            if u.path == "/oauth_token.do":
                return self._oauth_token(self._body())
            self._send(404, "{}")

        # ---------- IdP
        def _idp_get(self, path, q):
            if path != "/idp/authorize":
                return self._send(404, "")
            relay = q.get("RelayState", "")
            if self._cookies().get("IDP_SSO") in st.idp_sessions:  # silent SSO
                return self._redirect(f"{st.instance_url}/saml_consume?user=jdoe&RelayState={quote(relay)}")
            form = f"""<html><body><h2>Corporate SSO</h2>
              <form method=post action=/idp/login>
                <input name=RelayState type=hidden value="{relay}">
                <input id=username name=username><input id=password name=password type=password>
                <button id=submit type=submit>Sign in</button></form></body></html>"""
            self._send(200, form, ctype="text/html")

        def _saml_consume(self, q):
            if q.get("user") != "jdoe":
                return self._send(403, "")
            sid = secrets.token_hex(16)  # session fixation protection: new id after login
            st.sessions[sid] = {"auth": True, "token": secrets.token_hex(20)}
            relay = q.get("RelayState") or f"{st.instance_url}/navpage.do"
            self._redirect(relay, {"JSESSIONID": sid, "glide_session_store": secrets.token_hex(8)})

        # ---------- OAuth
        def _oauth_auth(self, q):
            sid, sess, set_c = self._session()
            if not sess["auth"]:
                relay = st.instance_url + self.path
                return self._redirect(f"{st.idp_url}/idp/authorize?RelayState={quote(relay)}", set_c)
            if q.get("client_id") not in st.oauth_clients:
                return self._send(400, "<p>invalid client</p>", ctype="text/html")
            code = secrets.token_hex(12)
            st.codes[code] = {"client_id": q["client_id"], "redirect_uri": q.get("redirect_uri"),
                              "challenge": q.get("code_challenge")}
            ru = q.get("redirect_uri", "")
            if ru.startswith("/"):
                return self._send(200, f"<html><body>Your code: <b id=code>{code}</b></body></html>", ctype="text/html")
            self._redirect(f"{ru}?{urlencode({'code': code, 'state': q.get('state', '')})}")

        def _oauth_token(self, f):
            gt = f.get("grant_type")
            if gt == "authorization_code":
                c = st.codes.pop(f.get("code", ""), None)
                if not c or c["client_id"] != f.get("client_id") or c["redirect_uri"] != f.get("redirect_uri"):
                    return self._json(401, {"error": "invalid_grant"})
                if _s256(f.get("code_verifier", "")) != c["challenge"]:
                    return self._json(401, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
            elif gt == "refresh_token":
                if f.get("refresh_token") not in st.refresh:
                    return self._json(401, {"error": "invalid_grant"})
                st.refresh.discard(f["refresh_token"])
            else:
                return self._json(400, {"error": "unsupported_grant_type"})
            at, rt = secrets.token_hex(16), secrets.token_hex(16)
            st.access[at] = 1
            st.refresh.add(rt)
            self._json(200, {"access_token": at, "refresh_token": rt, "token_type": "Bearer",
                             "expires_in": st.access_ttl, "scope": "useraccount"})

        # ---------- REST
        def _authed(self) -> tuple[bool, dict]:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:] in st.access, {}
            sid = self._cookies().get("JSESSIONID")
            sess = st.sessions.get(sid or "")
            if not sess or not sess["auth"]:
                return False, {}
            if self.headers.get("X-UserToken") != sess["token"]:
                return False, {}
            return True, {"glide_user_activity": secrets.token_hex(4)}

        def _api(self, path, q):
            ok, set_c = self._authed()
            if not ok:
                return self._json(401, {"error": {"message": "User Not Authenticated",
                                                  "detail": "Required to provide Auth information"}, "status": "failure"})
            if path == "/api/sn_km_api/knowledge/articles":
                words = q.get("query", "").lower().split()
                arts = [{"id": r["sys_id"], "number": r["number"], "title": r["short_description"],
                         "snippet": f"<b>{r['text'][:40]}</b>", "score": 9.5}
                        for r in DATA["kb_knowledge"] if all(w in json.dumps(r).lower() for w in words)]
                return self._json(200, {"result": {"meta": {"count": len(arts)}, "articles": arts}}, cookies=set_c)
            parts = path.split("/")  # ['', 'api', 'now', 'table', '<t>', '<sys_id>?']
            if len(parts) >= 5 and parts[1:4] == ["api", "now", "table"]:
                table = parts[4]
                rows = [USER] if table == "sys_user" else DATA.get(table)
                if rows is None:
                    return self._json(400, {"error": {"message": "Invalid table " + table}, "status": "failure"})
                if len(parts) == 6:
                    r = next((r for r in rows if r["sys_id"] == parts[5]), None)
                    if not r:
                        return self._json(404, {"error": {"message": "No Record found"}, "status": "failure"})
                    return self._json(200, {"result": r}, cookies=set_c)
                out = []
                for r in rows:
                    m = _match(r, q.get("sysparm_query", ""), table)
                    if m == "not-indexed":
                        return self._json(400, {"error": {"message": "Table is not text indexed"}, "status": "failure"})
                    if m:
                        out.append(r)
                off, lim = int(q.get("sysparm_offset", 0)), int(q.get("sysparm_limit", 10000))
                out = out[off:off + lim]
                if q.get("sysparm_fields"):
                    fl = q["sysparm_fields"].split(",")
                    out = [{k: r.get(k, "") for k in fl} for r in out]
                return self._json(200, {"result": out}, cookies=set_c)
            self._json(404, {"error": {"message": "not found"}})

    return H


class FakeServiceNow:
    def __init__(self):
        self.state = State()
        h = make_handler(self.state)
        self.inst = ThreadingHTTPServer(("127.0.0.1", 0), h)
        self.idp = ThreadingHTTPServer(("127.0.0.1", 0), h)
        self.state.instance_url = f"http://localhost:{self.inst.server_address[1]}"
        self.state.idp_url = f"http://127.0.0.1:{self.idp.server_address[1]}"
        self._threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self.inst, self.idp)]

    @property
    def url(self) -> str:
        return self.state.instance_url

    def __enter__(self):
        for t in self._threads:
            t.start()
        return self

    def __exit__(self, *a):
        for s in (self.inst, self.idp):
            s.shutdown()
            s.server_close()
