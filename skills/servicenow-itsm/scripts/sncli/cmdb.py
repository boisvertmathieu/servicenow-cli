"""Is a service offering linked to a configuration item?  CMDB path search.

A service offering reaches its CIs through the CMDB relationship table
(``cmdb_rel_ci``: offering -> application service -> application -> server ...)
and, for mapped / tag-based / dynamic application services, through the service
map table (``svc_ci_assoc``: service -> every CI of its map).  ``find_link`` runs
a bidirectional breadth-first search: downstream from the offering (parent ->
child) and upstream from the CI (child -> parent, plus the services whose map
contains it), one batched ``...IN<sys_ids>`` query per level, until both sides
meet.  It returns the shortest chain of relationships, so the answer is both
"yes / no" and "why".
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .client import ApiError, ServiceNowClient
from .search import SYS_ID_RE

REL_TABLE = "cmdb_rel_ci"
MAP_TABLE = "svc_ci_assoc"
CHUNK = 100            # sys_ids per IN clause (keeps URLs well under server limits)
ROWS_PER_QUERY = 5000  # hard cap per batched query; hitting it marks the search as truncated
REL_FIELDS = ["sys_id", "parent", "child", "type", "parent.sys_class_name", "child.sys_class_name"]
MAP_FIELDS = ["sys_id", "service_id", "ci_id", "service_id.sys_class_name", "ci_id.sys_class_name"]
NODE_FIELDS = ["sys_id", "name", "sys_class_name", "operational_status"]


def _val(row: dict[str, Any], field: str) -> str:
    v = row.get(field)
    if isinstance(v, dict):
        v = v.get("value")
    return "" if v is None else str(v)


def _disp(row: dict[str, Any], field: str) -> str:
    v = row.get(field)
    if isinstance(v, dict):
        v = v.get("display_value") if v.get("display_value") not in (None, "") else v.get("value")
    return "" if v is None else str(v)


def _node(row: dict[str, Any], client: ServiceNowClient, table: str) -> dict[str, Any]:
    n = {"sys_id": _val(row, "sys_id"), "name": _disp(row, "name"), "sys_class_name": _val(row, "sys_class_name"),
         "class": _disp(row, "sys_class_name"), "operational_status": _disp(row, "operational_status")}
    if row.get("parent") is not None:
        n["parent"] = _disp(row, "parent")
        n["parent_sys_id"] = _val(row, "parent")
    n["_url"] = client.record_url(n["sys_class_name"] or table, n["sys_id"])
    return n


def resolve(client: ServiceNowClient, table: str, ident: str, what: str, extra_fields: list[str] | None = None,
            ) -> dict[str, Any]:
    """Find exactly one record by sys_id, exact name, then name LIKE.  Ambiguity is an error."""
    fields = NODE_FIELDS + (extra_fields or [])
    ident = ident.strip()

    def q(query: str, limit: int = 11) -> list[dict[str, Any]]:
        return list(client.table(table, query=query, fields=fields, limit=limit, display_value="all"))

    if SYS_ID_RE.match(ident):
        rows = q(f"sys_id={ident}", 1)
    else:
        clean = ident.replace("^", " ")
        rows = q(f"name={clean}") or q(f"nameLIKE{clean}^ORDERBYname")
    # An unknown field is silently dropped by ServiceNow, which would return the whole table: re-check.
    if SYS_ID_RE.match(ident):
        rows = [r for r in rows if _val(r, "sys_id") == ident]
    else:
        rows = [r for r in rows if ident.lower() in _disp(r, "name").lower()]
    if not rows:
        raise LookupError(f"No {what} matching '{ident}' in {table}.")
    if len(rows) > 1:
        exact = [r for r in rows if _disp(r, "name").lower() == ident.lower()]
        if len(exact) == 1:
            rows = exact
        else:
            cands = "; ".join(f"{_disp(r, 'name')} [{_val(r, 'sys_class_name')}] {_val(r, 'sys_id')}"
                              for r in (exact or rows)[:10])
            more = " ..." if len(exact or rows) > 10 else ""
            raise LookupError(f"Several {what}s match '{ident}' in {table}: {cands}{more}. "
                              f"Pass a sys_id or a narrower table (--ci-table / --offering-table).")
    return _node(rows[0], client, table)


class _Side:
    """One half of the bidirectional search."""

    def __init__(self, root: str, name: str, cls: str, forward: bool):
        self.forward = forward
        self.prev: dict[str, tuple[str, dict[str, Any]] | None] = {root: None}  # node -> (neighbour, edge)
        self.info: dict[str, dict[str, str]] = {root: {"sys_id": root, "name": name, "sys_class_name": cls}}
        self.frontier: set[str] = {root}
        self.depth = 0


def _chunks(ids: set[str]) -> list[list[str]]:
    s = sorted(ids)
    return [s[i:i + CHUNK] for i in range(0, len(s), CHUNK)]


def _fetch(client: ServiceNowClient, table: str, field: str, ids: set[str], fields: list[str],
           extra: str) -> tuple[list[dict[str, Any]], bool]:
    def one(chunk: list[str]) -> tuple[list[dict[str, Any]], bool]:
        q = f"{field}IN{','.join(chunk)}" + (f"^{extra}" if extra else "")
        rows = list(client.table(table, query=q, fields=fields, limit=ROWS_PER_QUERY, display_value="all",
                                 page_size=1000))
        # Defensive: keep only rows that really match (protects against a silently ignored condition).
        keep = set(chunk)
        return [r for r in rows if _val(r, field) in keep], len(rows) >= ROWS_PER_QUERY

    parts = _chunks(ids)
    with ThreadPoolExecutor(max_workers=min(6, len(parts) or 1)) as ex:
        results = list(ex.map(one, parts))
    return [r for rows, _ in results for r in rows], any(t for _, t in results)


def _excluded(type_name: str, patterns: list[str]) -> bool:
    t = type_name.lower()
    return any(p.lower() in t for p in patterns)


def find_link(client: ServiceNowClient, offering: dict[str, Any], ci: dict[str, Any], *, max_depth: int = 6,
              direction: str = "down", use_service_map: bool = True, exclude_types: list[str] | None = None,
              max_nodes: int = 5000) -> dict[str, Any]:
    """Shortest relationship chain from *offering* to *ci* (dicts from :func:`resolve`)."""
    exclude_types = exclude_types or []
    undirected = direction == "any"
    stats: dict[str, Any] = {"max_depth": max_depth, "direction": direction, "service_map": use_service_map,
                             "exclude_types": exclude_types, "queries": 0, "truncated": False, "warnings": []}
    result: dict[str, Any] = {"linked": False, "link": None, "depth": None, "chain": [], "steps": [],
                              "offering": offering, "ci": ci, "search": stats}

    if offering["sys_id"] == ci["sys_id"]:
        result.update(linked=True, link="same-record", depth=0, chain=[offering["name"]])
        return result
    if offering.get("parent_sys_id") and offering["parent_sys_id"] == ci["sys_id"]:
        step = {"parent": _short(ci), "type": "parent (service of the offering)", "child": _short(offering),
                "source": "service_offering.parent"}
        result.update(linked=True, link="offering-parent", depth=1, chain=[offering["name"], ci["name"]],
                      steps=[step])
        return result

    fwd = _Side(offering["sys_id"], offering["name"], offering["sys_class_name"], forward=True)
    bwd = _Side(ci["sys_id"], ci["name"], ci["sys_class_name"], forward=False)
    map_ok = use_service_map

    def expand(side: _Side) -> str | None:
        nonlocal map_ok
        other = bwd if side is fwd else fwd
        frontier, side.frontier = side.frontier, set()
        side.depth += 1
        edges: list[tuple[str, str, dict[str, Any]]] = []  # (from_node, to_node, edge) in search direction
        # forward side walks parent -> child ; backward side walks child -> parent
        lookups = [("parent", "child")] if side.forward else [("child", "parent")]
        if undirected:
            lookups = [("parent", "child"), ("child", "parent")]
        for near, far in lookups:
            rows, trunc = _fetch(client, REL_TABLE, near, frontier, REL_FIELDS, "")
            stats["queries"] += len(_chunks(frontier))
            stats["truncated"] |= trunc
            for r in rows:
                t = _disp(r, "type")
                if _excluded(t, exclude_types):
                    continue
                a, b = _val(r, near), _val(r, far)
                if not b:
                    continue
                side.info.setdefault(b, {"sys_id": b, "name": _disp(r, far),
                                         "sys_class_name": _val(r, f"{far}.sys_class_name")})
                edge = {"parent": _val(r, "parent"), "child": _val(r, "child"), "type": t, "source": REL_TABLE,
                        "rel_sys_id": _val(r, "sys_id")}
                edges.append((a, b, edge))
        if not side.forward and map_ok:
            try:
                rows, trunc = _fetch(client, MAP_TABLE, "ci_id", frontier, MAP_FIELDS, "")
                stats["queries"] += len(_chunks(frontier))
                stats["truncated"] |= trunc
                for r in rows:
                    a, b = _val(r, "ci_id"), _val(r, "service_id")
                    if not b:
                        continue
                    side.info.setdefault(b, {"sys_id": b, "name": _disp(r, "service_id"),
                                             "sys_class_name": _val(r, "service_id.sys_class_name")})
                    edges.append((a, b, {"parent": b, "child": a, "type": "service map (contains CI)",
                                         "source": MAP_TABLE, "rel_sys_id": _val(r, "sys_id")}))
            except ApiError as e:
                map_ok = False
                stats["warnings"].append(f"{MAP_TABLE} not readable ({e}); service maps were not searched.")
        for a, b, edge in edges:
            if b in side.prev:
                continue
            side.prev[b] = (a, edge)
            if b in other.prev:
                return b
            side.frontier.add(b)
        if len(side.prev) > max_nodes:
            stats["truncated"] = True
            stats["warnings"].append(f"Stopped: more than {max_nodes} CIs reached on the "
                                     f"{'offering' if side.forward else 'CI'} side (--max-nodes).")
            side.frontier = set()
        return None

    meet: str | None = None
    while fwd.depth + bwd.depth < max_depth and (fwd.frontier or bwd.frontier):
        side = fwd if (fwd.frontier and (not bwd.frontier or len(fwd.frontier) <= len(bwd.frontier))) else bwd
        meet = expand(side)
        if meet:
            break

    stats["reached_from_offering"] = len(fwd.prev) - 1
    stats["reached_from_ci"] = len(bwd.prev) - 1
    stats["service_map"] = map_ok
    if not meet:
        stats["exhausted"] = not fwd.frontier and not bwd.frontier
        return result

    # Rebuild: offering ... meet (forward side), then meet ... ci (backward side).
    nodes_f, steps_f = [meet], []
    n = meet
    while fwd.prev[n] is not None:
        n, edge = fwd.prev[n]  # type: ignore[misc]
        nodes_f.append(n)
        steps_f.append(edge)
    nodes_f.reverse()
    steps_f.reverse()
    nodes_b, steps_b = [], []
    n = meet
    while bwd.prev[n] is not None:
        n, edge = bwd.prev[n]  # type: ignore[misc]
        nodes_b.append(n)
        steps_b.append(edge)
    info = {**bwd.info, **fwd.info}
    chain_ids = nodes_f + nodes_b
    steps = []
    for e in steps_f + steps_b:
        steps.append({"parent": _short(info.get(e["parent"], {"sys_id": e["parent"]})), "type": e["type"],
                      "child": _short(info.get(e["child"], {"sys_id": e["child"]})), "source": e["source"],
                      "rel_sys_id": e["rel_sys_id"]})
    result.update(linked=True, link="direct" if len(steps) == 1 else "indirect", depth=len(steps),
                  chain=[info.get(i, {}).get("name") or i for i in chain_ids], steps=steps)
    return result


def _short(n: dict[str, Any]) -> dict[str, str]:
    return {"name": n.get("name", ""), "sys_class_name": n.get("sys_class_name", ""), "sys_id": n.get("sys_id", "")}


def describe(result: dict[str, Any]) -> list[str]:
    """Human-readable summary lines."""
    o, c, s = result["offering"], result["ci"], result["search"]
    lines = [f"Offering : {o['name']} [{o['sys_class_name']}]" + (f"  (service: {o['parent']})" if o.get("parent") else ""),
             f"CI       : {c['name']} [{c['sys_class_name']}]"]
    if result["linked"]:
        lines.append(f"LINKED ({result['link']}, {result['depth']} relationship(s))")
        for st in result["steps"]:
            lines.append(f"  {st['parent']['name']} [{st['parent']['sys_class_name']}]"
                         f"  --{st['type']}-->  {st['child']['name']} [{st['child']['sys_class_name']}]"
                         f"   ({st['source']})")
    else:
        why = "search exhausted" if s.get("exhausted") else f"depth limit {s['max_depth']} reached"
        if s["truncated"]:
            why = "search truncated"
        lines.append(f"NOT LINKED within {s['max_depth']} relationship(s), direction={s['direction']} ({why}).")
        lines.append(f"  CIs reached: {s.get('reached_from_offering', 0)} from the offering, "
                     f"{s.get('reached_from_ci', 0)} from the CI.")
    for w in s["warnings"]:
        lines.append(f"  warning: {w}")
    return lines

