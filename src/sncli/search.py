"""Search helpers built on the Table API and the Knowledge Management API."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .client import ApiError, ServiceNowClient

# Fields shown by default per table (all fetched with display values).
FIELD_PRESETS: dict[str, list[str]] = {
    "incident": ["number", "short_description", "state", "priority", "assigned_to", "assignment_group", "sys_updated_on"],
    "sc_req_item": ["number", "short_description", "state", "cat_item", "requested_for", "sys_updated_on"],
    "sc_request": ["number", "short_description", "request_state", "requested_for", "sys_updated_on"],
    "sc_task": ["number", "short_description", "state", "assigned_to", "sys_updated_on"],
    "change_request": ["number", "short_description", "state", "type", "start_date", "assigned_to"],
    "problem": ["number", "short_description", "state", "priority", "assigned_to", "sys_updated_on"],
    "kb_knowledge": ["number", "short_description", "workflow_state", "kb_knowledge_base", "sys_updated_on"],
    "task": ["number", "short_description", "state", "sys_class_name", "assigned_to", "sys_updated_on"],
    "sys_user": ["user_name", "name", "email", "title", "department"],
    "sys_user_group": ["name", "description", "manager", "email"],
    "cmdb_ci": ["name", "sys_class_name", "operational_status", "owned_by"],
}
DEFAULT_FIELDS = ["number", "short_description", "state", "sys_updated_on"]

# Record-number prefixes -> table (default numbering; instances may customise).
PREFIX_TABLE = {
    "INC": "incident", "RITM": "sc_req_item", "REQ": "sc_request", "SCTASK": "sc_task", "TASK": "task",
    "CHG": "change_request", "CTASK": "change_task", "PRB": "problem", "PTASK": "problem_task",
    "KB": "kb_knowledge",
}
SYS_ID_RE = re.compile(r"^[0-9a-f]{32}$")
NUMBER_RE = re.compile(r"^([A-Z]+)\d+$")

# ServiceNow's keyword (Zing) search token for sysparm_query on text-indexed tables.
TEXT_QUERY = "123TEXTQUERY321"


def fields_for(table: str, extra: list[str] | None = None) -> list[str]:
    base = list(extra) if extra else list(FIELD_PRESETS.get(table, DEFAULT_FIELDS))
    if "sys_id" not in base:
        base.append("sys_id")
    return base


def table_for_number(number: str) -> str | None:
    m = NUMBER_RE.match(number.upper())
    return PREFIX_TABLE.get(m.group(1)) if m else None


def _escape(text: str) -> str:
    # '^' separates encoded-query terms; there is no escape, so strip it.
    return text.replace("^", " ").strip()


def build_text_query(text: str, extra: str = "", like: bool = False, like_fields: list[str] | None = None) -> str:
    t = _escape(text)
    if like:
        cols = like_fields or ["short_description", "description"]
        q = "^OR".join(f"{c}LIKE{t}" for c in cols)
    else:
        q = f"{TEXT_QUERY}={t}"
    return f"{q}^{extra}" if extra else q


def search_table(client: ServiceNowClient, table: str, text: str, *, limit: int = 10, extra_query: str = "",
                 fields: list[str] | None = None, like: bool = False) -> dict[str, Any]:
    flds = fields_for(table, fields)
    mode = "like" if like else "text"
    try:
        rows = list(client.table(table, query=build_text_query(text, extra_query, like), fields=flds, limit=limit))
    except ApiError as e:
        if like or e.status not in (400, 403, 500):
            return {"table": table, "rows": [], "error": str(e), "mode": mode}
        # Table not text-indexed (or keyword search disabled) -> LIKE fallback on description fields.
        like_fields = ["short_description", "description"] if "short_description" in flds else ["name"]
        try:
            rows = list(client.table(table, query=build_text_query(text, extra_query, True, like_fields),
                                     fields=flds, limit=limit))
            mode = "like"
        except ApiError as e2:
            return {"table": table, "rows": [], "error": str(e2), "mode": "like"}
    for r in rows:
        if r.get("sys_id"):
            r["_url"] = client.record_url(table, r["sys_id"])
    return {"table": table, "rows": rows, "error": None, "mode": mode}


def multi_search(client: ServiceNowClient, tables: list[str], text: str, **kw) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=min(6, len(tables) or 1)) as ex:
        return list(ex.map(lambda t: search_table(client, t, text, **kw), tables))


def get_record(client: ServiceNowClient, ident: str, table: str | None = None,
               fields: list[str] | None = None) -> tuple[str, dict[str, Any]]:
    table = table or table_for_number(ident)
    if not table:
        raise ValueError(f"Cannot infer the table for '{ident}'; pass it explicitly: sn get <table> {ident}")
    if SYS_ID_RE.match(ident):
        rec = client.record(table, ident)
    else:
        rows = list(client.table(table, query=f"number={ident.upper()}", limit=1,
                                 fields=fields if fields else None))
        rec = rows[0] if rows else {}
    if not rec:
        raise LookupError(f"{ident} not found in {table}")
    if fields:
        rec = {k: rec.get(k) for k in fields_for(table, fields)}
    if rec.get("sys_id"):
        rec["_url"] = client.record_url(table, rec["sys_id"])
    return table, rec


def kb_search(client: ServiceNowClient, text: str, limit: int = 10) -> dict[str, Any]:
    """Knowledge Management REST API (sn_km_api) with a Table API fallback."""
    try:
        data = client.get_json("/api/sn_km_api/knowledge/articles",
                               {"query": text, "limit": limit, "fields": "short_description,kb_knowledge_base,sys_updated_on"})
        res = data.get("result") or {}
        rows = []
        for a in res.get("articles") or []:
            rows.append({
                "number": a.get("number"), "short_description": a.get("title") or a.get("short_description"),
                "snippet": re.sub(r"<[^>]+>", "", a.get("snippet") or "").strip(),
                "score": a.get("score"), "sys_id": a.get("id") or a.get("sys_id"),
            })
        for r in rows:
            if r.get("sys_id"):
                r["_url"] = f"{client.instance}/kb_view.do?sys_kb_id={r['sys_id']}"
        return {"table": "kb (Knowledge API)", "rows": rows, "error": None, "mode": "kb-api"}
    except ApiError as e:
        if e.status not in (400, 403, 404):
            raise
    r = search_table(client, "kb_knowledge", text, limit=limit, extra_query="workflow_state=published")
    for row in r["rows"]:
        row["_url"] = f"{client.instance}/kb_view.do?sys_kb_id={row['sys_id']}"
    return r
