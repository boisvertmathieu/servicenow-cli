"""`sn` - search your ServiceNow instance from the terminal, behind corporate SSO."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import click

from . import __version__, store
from .auth.session import (fetch_user_token, looks_like_session, make_session_creds, parse_cookie_header,
                           validate_session)
from .client import ApiError, AuthRequired, ServiceNowClient, tls_verify
from .config import Config, OAuthSettings, Profile, normalize_instance
from .output import emit_record, emit_rows, err
from .search import get_record, kb_search, multi_search
from .wsl import is_wsl, open_url

FORMATS = click.Choice(["table", "json", "csv"])


class Ctx:
    def __init__(self, profile_name: str | None):
        self.config = Config.load()
        self.profile: Profile = self.config.profile(profile_name)

    def require_instance(self, override: str | None = None) -> str:
        if override:
            self.profile.instance = normalize_instance(override)
        if not self.profile.instance:
            raise click.UsageError("No instance configured. Run: sn configure --instance <name|url>")
        return self.profile.instance

    def client(self) -> ServiceNowClient:
        try:
            return ServiceNowClient(self.profile)
        except AuthRequired as e:
            raise click.ClickException(str(e))


pass_ctx = click.make_pass_decorator(Ctx)


def _fail_on_api_errors(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except (AuthRequired, ApiError, LookupError, ValueError) as e:
            raise click.ClickException(str(e))
    return wrapper


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--profile", "-P", envvar="SN_PROFILE", help="Configuration profile (default: 'default').")
@click.version_option(__version__, prog_name="sn")
@click.pass_context
def main(ctx: click.Context, profile: str | None):
    """Search a ServiceNow instance from the terminal (SSO-friendly).

    \b
    Quick start:
      sn configure --instance acme          # acme.service-now.com
      sn login                              # browser SSO
      sn search "vpn outage"
      sn get INC0012345
    """
    ctx.obj = Ctx(profile)


# --------------------------------------------------------------------------- configure
@main.command()
@click.option("--instance", "-i", help="Instance name (acme), host, or URL.")
@click.option("--auth-method", type=click.Choice(["browser", "oauth", "cookie"]), help="Default login method.")
@click.option("--sso-login-path", help="Path that starts SSO, e.g. 'login_with_sso.do?glide_sso_id=<sys_id>'.")
@click.option("--oauth-client-id", help="Client ID of a public OAuth app registered on the instance.")
@click.option("--oauth-redirect-uri", help="Redirect URI registered for that app (default http://localhost:8765/callback).")
@click.option("--oauth-scope", help="OAuth scope (usually empty / 'useraccount').")
@click.option("--tables", help="Comma-separated default tables for `sn search`.")
@click.option("--make-default", is_flag=True, help="Make this profile the default one.")
@click.option("--show", is_flag=True, help="Print the effective configuration.")
@pass_ctx
def configure(c: Ctx, instance, auth_method, sso_login_path, oauth_client_id, oauth_redirect_uri, oauth_scope,
              tables, make_default, show):
    """Create or update a profile."""
    p = c.profile
    if instance:
        p.instance = normalize_instance(instance)
    if auth_method:
        p.auth_method = auth_method
    if sso_login_path is not None:
        p.sso_login_path = sso_login_path or None
    if oauth_client_id:
        p.oauth.client_id = oauth_client_id
    if oauth_redirect_uri:
        p.oauth.redirect_uri = oauth_redirect_uri
    if oauth_scope is not None:
        p.oauth.scope = oauth_scope
    if tables:
        p.default_tables = [t.strip() for t in tables.split(",") if t.strip()]
    changed = any(v is not None and v is not False for v in
                  (instance, auth_method, sso_login_path, oauth_client_id, oauth_redirect_uri, oauth_scope, tables,
                   make_default or None))
    if changed:
        c.config.put_profile(p, make_default)
        c.config.save()
        err.print(f"[green]Saved profile '{p.name}'.[/green]")
    if show or not changed:
        print(json.dumps({"profile": p.name, **p.to_dict()}, indent=2))


# --------------------------------------------------------------------------- login / logout
def _status(msg: str) -> None:
    err.print(f"[cyan]{msg}[/cyan]")


@main.command()
@click.option("--method", "-m", type=click.Choice(["browser", "oauth", "cookie"]),
              help="browser: SSO in a browser window (default) | oauth: OAuth+PKCE | cookie: paste a Cookie header.")
@click.option("--instance", "-i", help="Override the profile's instance.")
@click.option("--sdk-client", is_flag=True,
              help="OAuth with ServiceNow SDK's public client (copy/paste code). Only if your instance allows it.")
@click.option("--cookie", "cookie_value", help="Cookie header for --method cookie (otherwise prompted, hidden).")
@click.option("--user-token", help="X-UserToken (g_ck) for --method cookie; normally derived automatically.")
@click.option("--timeout", default=300, show_default=True, help="Seconds to wait for sign-in.")
@pass_ctx
def login(c: Ctx, method, instance, sdk_client, cookie_value, user_token, timeout):
    """Sign in through your corporate SSO and store the session."""
    inst = c.require_instance(instance)
    method = method or ("oauth" if sdk_client else c.profile.auth_method)
    verify = tls_verify()
    try:
        if method == "browser":
            from .auth.browser import browser_login
            _status(f"Opening a browser window on {inst} ...")
            creds = browser_login(inst, c.profile.name, login_path=c.profile.sso_login_path, timeout=timeout,
                                  verify=verify, status=_status)
        elif method == "oauth":
            from .auth import oauth
            import os
            s = OAuthSettings(**vars(c.profile.oauth))
            s.client_secret = os.environ.get("SN_OAUTH_CLIENT_SECRET") or s.client_secret
            if sdk_client:
                s.client_id, s.redirect_uri, s.scope = oauth.SDK_PUBLIC_CLIENT_ID, oauth.SDK_REDIRECT_PATH, ""
            creds = oauth.oauth_login(
                inst, s, verify=verify, timeout=timeout, status=_status,
                prompt_code=lambda: click.prompt("Paste the code shown in the browser", hide_input=True))
        else:
            host = inst.split("://", 1)[1].split("/")[0].split(":")[0]
            if not cookie_value:
                err.print("In your browser (signed in to ServiceNow): DevTools > Network > any request to the "
                          "instance > Request Headers > copy the whole 'Cookie' value.")
                cookie_value = click.prompt("Cookie", hide_input=True)
            cookies = parse_cookie_header(cookie_value, host)
            if not looks_like_session(cookies):
                raise click.ClickException("That does not look like a ServiceNow session cookie (no JSESSIONID / glide_*).")
            token = user_token
            if not token:
                token, cookies = fetch_user_token(inst, cookies, verify=verify)
            creds = make_session_creds(inst, cookies, token or "", source="cookie")
            user = validate_session(inst, cookies, token or "", verify=verify)
            if not user:
                raise click.ClickException("The session was rejected by the instance (expired or guest).")
            creds["user"] = user
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(str(e))

    where = store.save(c.profile.name, creds)
    if not c.config.data.get("profiles", {}).get(c.profile.name):
        c.config.put_profile(c.profile)
        c.config.save()
    try:
        with ServiceNowClient(c.profile, creds) as cl:
            who = cl.whoami()
        err.print(f"[green]Signed in to {inst} as {who.get('name')} ({who.get('user_name')}).[/green]")
    except Exception as e:
        err.print(f"[yellow]Credentials stored but verification failed: {e}[/yellow]")
    err.print(f"[dim]Credentials stored in {where}[/dim]")


@main.command()
@click.option("--forget-browser", is_flag=True, help="Also delete the saved browser profile (IdP cookies).")
@pass_ctx
def logout(c: Ctx, forget_browser):
    """Forget stored credentials for the profile."""
    import shutil
    from .config import state_dir
    store.delete(c.profile.name)
    if forget_browser:
        shutil.rmtree(state_dir() / "browser-profile" / c.profile.name, ignore_errors=True)
    err.print(f"Logged out of profile '{c.profile.name}'.")


@main.command()
@click.option("--format", "fmt", type=FORMATS, default="table")
@pass_ctx
@_fail_on_api_errors
def whoami(c: Ctx, fmt):
    """Show the signed-in user (verifies the session)."""
    with c.client() as cl:
        emit_record({**cl.whoami(), "instance": cl.instance}, fmt)


@main.command()
@pass_ctx
def status(c: Ctx):
    """Show profile, auth method and token expiry without calling the instance."""
    creds = store.load(c.profile.name)
    info: dict[str, Any] = {"profile": c.profile.name, "instance": c.profile.instance, "wsl": is_wsl()}
    if creds:
        info["auth"] = creds["type"] + (f" ({creds.get('source')})" if creds.get("source") else "")
        info["obtained"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(creds.get("obtained_at", 0)))
        if creds.get("expires_at"):
            info["access_token_expires_in_s"] = int(creds["expires_at"] - time.time())
            info["refreshable"] = bool(creds.get("refresh_token"))
        if creds.get("user"):
            info["user"] = creds["user"].get("user_name")
    else:
        info["auth"] = "not signed in"
    print(json.dumps(info, indent=2))


# --------------------------------------------------------------------------- search & read
def _split(values: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for v in values:
        out.extend(x.strip() for x in v.split(",") if x.strip())
    return out


@main.command()
@click.argument("text", nargs=-1, required=True)
@click.option("--table", "-t", multiple=True, help="Table(s) to search (repeat or comma-separate).")
@click.option("--limit", "-n", default=10, show_default=True, help="Max results per table.")
@click.option("--query", "-q", "extra", default="", help="Extra encoded query, e.g. 'active=true^priority<=2'.")
@click.option("--fields", "-f", multiple=True, help="Fields to return (comma-separated).")
@click.option("--like", is_flag=True, help="Use LIKE on short_description/description instead of keyword search.")
@click.option("--url", "show_url", is_flag=True, help="Show a link to each record.")
@click.option("--format", "fmt", type=FORMATS, default="table")
@pass_ctx
@_fail_on_api_errors
def search(c: Ctx, text, table, limit, extra, fields, like, show_url, fmt):
    """Keyword search across several tables (incidents, requests, changes, KB...)."""
    tables = _split(table) or c.profile.default_tables
    q = " ".join(text)
    with c.client() as cl:
        results = multi_search(cl, tables, q, limit=limit, extra_query=extra, fields=_split(fields) or None,
                               like=like)
    if fmt == "json":
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return
    total = 0
    for r in results:
        if r["error"]:
            err.print(f"[yellow]{r['table']}: {r['error']}[/yellow]")
            continue
        total += len(r["rows"])
        if r["rows"]:
            if fmt == "csv":
                for row in r["rows"]:
                    row["_table"] = r["table"]
            emit_rows(r["rows"], fmt, title=f"{r['table']} ({len(r['rows'])}{', LIKE' if r['mode']=='like' else ''})",
                      show_url=show_url)
    if fmt == "table":
        err.print(f"[dim]{total} result(s) for '{q}' in {', '.join(tables)}[/dim]")


@main.command()
@click.argument("table")
@click.option("--query", "-q", default="", help="Encoded query (copy it from a list view: right-click the breadcrumb > Copy query).")
@click.option("--fields", "-f", multiple=True, help="Fields to return (comma-separated).")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--order-by", "-o", help="Field to sort by; prefix with '-' for descending.")
@click.option("--raw-values", is_flag=True, help="Return raw values instead of display values.")
@click.option("--url", "show_url", is_flag=True)
@click.option("--format", "fmt", type=FORMATS, default="table")
@pass_ctx
@_fail_on_api_errors
def query(c: Ctx, table, query, fields, limit, order_by, raw_values, show_url, fmt):
    """Run an encoded query against any table."""
    from .search import fields_for
    q = query
    if order_by:
        q += ("^" if q else "") + (f"ORDERBYDESC{order_by[1:]}" if order_by.startswith("-") else f"ORDERBY{order_by}")
    with c.client() as cl:
        rows = list(cl.table(table, query=q, fields=fields_for(table, _split(fields) or None), limit=limit,
                             display_value="false" if raw_values else "true"))
        for r in rows:
            if r.get("sys_id"):
                r["_url"] = cl.record_url(table, r["sys_id"])
    emit_rows(rows, fmt, title=f"{table} ({len(rows)})", show_url=show_url)


@main.command()
@click.argument("args", nargs=-1, required=True)
@click.option("--fields", "-f", multiple=True, help="Only these fields.")
@click.option("--format", "fmt", type=FORMATS, default="table")
@pass_ctx
@_fail_on_api_errors
def get(c: Ctx, args, fields, fmt):
    """Show one record:  sn get INC0012345   |   sn get <table> <number|sys_id>"""
    table, ident = (None, args[0]) if len(args) == 1 else (args[0], args[1])
    with c.client() as cl:
        _, rec = get_record(cl, ident, table, _split(fields) or None)
    emit_record(rec, "json" if fmt == "json" else "table")


@main.command()
@click.argument("text", nargs=-1, required=True)
@click.option("--limit", "-n", default=10, show_default=True)
@click.option("--url", "show_url", is_flag=True)
@click.option("--format", "fmt", type=FORMATS, default="table")
@pass_ctx
@_fail_on_api_errors
def kb(c: Ctx, text, limit, show_url, fmt):
    """Search knowledge articles (Knowledge API, falls back to kb_knowledge)."""
    with c.client() as cl:
        r = kb_search(cl, " ".join(text), limit)
    emit_rows(r["rows"], fmt, title=f"{r['table']} ({len(r['rows'])})", show_url=show_url)


@main.command("open")
@click.argument("args", nargs=-1, required=True)
@pass_ctx
@_fail_on_api_errors
def open_cmd(c: Ctx, args):
    """Open a record in your browser:  sn open INC0012345"""
    table, ident = (None, args[0]) if len(args) == 1 else (args[0], args[1])
    with c.client() as cl:
        _, rec = get_record(cl, ident, table, ["number"])
    print(rec["_url"])
    open_url(rec["_url"])


@main.command()
@click.argument("path")
@click.option("--method", "-X", default="GET", show_default=True)
@click.option("--param", "-p", multiple=True, help="Query parameter key=value (repeatable).")
@click.option("--data", "-d", help="JSON request body.")
@pass_ctx
@_fail_on_api_errors
def api(c: Ctx, path, method, param, data):
    """Call any REST endpoint with your session, e.g. sn api /api/now/table/sys_user -p sysparm_limit=1"""
    params = dict(p.split("=", 1) for p in param)
    kwargs: dict[str, Any] = {"params": params}
    if data:
        kwargs["json"] = json.loads(data)
    with c.client() as cl:
        r = cl.request(method.upper(), "/" + path.lstrip("/"), **kwargs)
    try:
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))
    except ValueError:
        sys.stdout.write(r.text)


if __name__ == "__main__":  # pragma: no cover
    main()
