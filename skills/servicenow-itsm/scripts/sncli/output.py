"""Rendering: rich tables for humans, JSON / CSV for scripts."""

from __future__ import annotations

import csv
import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()
err = Console(stderr=True)


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):
        return str(v.get("display_value") or v.get("value") or "")
    return str(v)


def emit_rows(rows: list[dict[str, Any]], fmt: str, title: str | None = None, show_url: bool = False) -> None:
    if fmt == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols and (show_url or k != "_url") and k != "sys_id":
                cols.append(k)
    if fmt == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(cols)
        for r in rows:
            w.writerow([_cell(r.get(c)) for c in cols])
        return
    t = Table(title=title, show_lines=False, header_style="bold cyan", title_justify="left")
    for c in cols:
        t.add_column("url" if c == "_url" else c, overflow="fold", no_wrap=c in ("number", "user_name"),
                     min_width=len(c) if c in ("number", "user_name", "short_description", "assignment_group") else None,
                     max_width=60 if c in ("short_description", "snippet") else None)
    for r in rows:
        t.add_row(*[_cell(r.get(c)) for c in cols])
    console.print(t)


def emit_record(rec: dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        return
    t = Table(show_header=False, box=None, pad_edge=False)
    t.add_column(style="bold cyan", no_wrap=True)
    t.add_column(overflow="fold")
    for k in sorted(rec, key=lambda k: (k.startswith("_"), k)):
        v = _cell(rec[k])
        if v != "":
            t.add_row("url" if k == "_url" else k, v)
    console.print(t)
