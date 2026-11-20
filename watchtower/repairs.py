"""Before vs after: what a repair changed, shown side by side in the Agent Theater.

Every repair (successful or not) is recorded with the list page as it looked when the old recipe
worked and as it looks now. The two pages are rendered for the dashboard with three highlights:
  - wt-item  (green):  what the recipe reads on that page (old recipe on the old page, new on the new)
  - wt-gone  (red):    structure that disappeared (e.g. the old table, a renamed class)
  - wt-new   (amber):  structure that appeared (e.g. the new cards)
Rendered pages are inert: scripts, frames and event handlers are removed, and the response forbids scripts.
"""

from __future__ import annotations

import html as H
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser, LexborNode

log = logging.getLogger("watchtower.repairs")

KEEP = 20                  # repair records kept per portal
MAX_MARKS = 300            # highlighted elements per page (keeps big pages readable)
SKIP_TAGS = {"html", "head", "body", "-text", "-comment", "br", "wbr", "hr"}
STRIP = "script, noscript, iframe, frame, frameset, object, embed, template, meta[http-equiv], link[rel=preload], link[rel=prefetch]"
STYLE = """<style id="wt-style">
.wt-item{background:rgba(63,185,80,.18)!important;box-shadow:inset 5px 0 0 #3fb950!important}
.wt-gone{outline:3px dashed #f85149!important;outline-offset:-2px;background-color:rgba(248,81,73,.10)!important}
.wt-new{outline:3px solid #d29922!important;outline-offset:-2px;background-color:rgba(210,153,34,.12)!important}
</style>"""

# Page-content security for rendered snapshots: no scripts, no forms, no frames, nothing executes.
CSP = "sandbox; default-src 'none'; style-src * 'unsafe-inline'; img-src * data:; font-src * data:"


def _file(data_dir: Path, pid: str) -> Path:
    return Path(data_dir) / "snapshots" / pid / "repairs.json"


def _read_html(data_dir: Path, ref: str | None) -> str | None:
    if not ref:
        return None
    f = (Path(data_dir) / ref).resolve()
    root = (Path(data_dir) / "snapshots").resolve()
    if root not in f.parents or not f.exists():
        return None
    return f.read_text(errors="replace")


def _sig(n: LexborNode) -> str:
    """Structural signature: tag plus its classes. Text is ignored, so new tenders are not 'changes'."""
    cls = sorted((n.attributes.get("class") or "").split())
    return n.tag + ("." + ".".join(cls) if cls else "")


def signatures(html: str) -> set[str]:
    tree = LexborHTMLParser(html or "")
    root = tree.body or tree.root
    if root is None:
        return set()
    return {_sig(n) for n in root.traverse() if n.tag not in SKIP_TAGS}


def _html_items(selector: str | None, source: str | None) -> str | None:
    """The recipe's item selector, if it can be shown on an HTML page (CSS on an html recipe)."""
    if not selector or source == "json_api" or selector.startswith(("/", "./", "$")):
        return None
    return selector


def render(html: str, *, other_html: str, item_selector: str | None, side: str, base_url: str | None) -> tuple[str, dict]:
    """Inert copy of the page with highlights. side='before' marks what disappeared, side='after' what appeared."""
    tree = LexborHTMLParser(html or "<html><body></body></html>")
    for n in tree.css(STRIP):
        n.decompose()
    root = tree.body or tree.root
    other = signatures(other_html)

    # 1. structural changes: count every changed element, draw a box only around the outermost ones
    changed: list[LexborNode] = []
    marked: set[int] = set()
    n_changed = 0
    if root is not None:
        for n in root.traverse():
            if n.tag in SKIP_TAGS or _sig(n) in other:
                continue
            n_changed += 1
            if len(changed) >= MAX_MARKS:
                continue
            p, inside = n.parent, False
            while p is not None:
                if p.mem_id in marked:
                    inside = True
                    break
                p = p.parent
            if not inside:
                marked.add(n.mem_id)
                changed.append(n)

    # 2. what the recipe reads on this page
    items: list[LexborNode] = []
    sel = item_selector
    if sel:
        try:
            items = tree.css(sel)
        except Exception:  # an exotic selector the CSS engine rejects: show the page without item marks
            items = []

    # 3. inert: drop event handlers and javascript: links
    if root is not None:
        for n in root.traverse():
            for k in [k for k in n.attributes if k.lower().startswith("on")]:
                del n.attrs[k]
            for k in ("href", "src", "action", "formaction"):
                v = n.attributes.get(k)
                if v and v.strip().lower().startswith("javascript:"):
                    del n.attrs[k]

    cls = "wt-gone" if side == "before" else "wt-new"
    for n in changed:
        n.attrs["class"] = ((n.attributes.get("class") or "") + " " + cls).strip()
    for n in items:
        n.attrs["class"] = ((n.attributes.get("class") or "") + " wt-item").strip()

    out = tree.html or ""
    head = (f'<base href="{H.escape(base_url, quote=True)}">' if base_url else "") + STYLE
    if re.search(r"<head[^>]*>", out, re.I):
        out = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + head, out, count=1, flags=re.I)
    else:
        out = head + out
    return out, {"changed": n_changed, "boxes": len(changed), "items": len(items)}


def record(data_dir: Path, pid: str, *, ok: bool, reason: str | None, diff: list[str], attempts: int,
           before_ref: str | None, before_url: str | None, before_recipe: dict | None,
           after_ref: str | None, after_url: str | None, after_recipe: dict | None) -> dict | None:
    """Append one repair to the portal's history (never raises: this is display data)."""
    try:
        f = _file(data_dir, pid)
        f.parent.mkdir(parents=True, exist_ok=True)
        rows = json.loads(f.read_text()) if f.exists() else []
        before_html = _read_html(data_dir, before_ref) or ""
        after_html = _read_html(data_dir, after_ref) or ""

        def side(ref, url, rec, html, other, name):
            sel = _html_items((rec or {}).get("item"), (rec or {}).get("source"))
            stats = render(html, other_html=other, item_selector=sel, side=name, base_url=url)[1] if html else {}
            return {"ref": ref, "url": url, "item": (rec or {}).get("item"), "source": (rec or {}).get("source"),
                    "items": stats.get("items", 0), "changed": stats.get("changed", 0)}

        row = {
            "n": (rows[-1]["n"] + 1) if rows else 1,
            "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "ok": ok, "reason": reason, "attempts": attempts, "diff": diff[:30],
            "before": side(before_ref, before_url, before_recipe, before_html, after_html, "before"),
            "after": side(after_ref, after_url, after_recipe, after_html, before_html, "after"),
        }
        rows = (rows + [row])[-KEEP:]
        f.write_text(json.dumps(rows, ensure_ascii=False))
        return row
    except Exception:  # noqa: BLE001
        log.exception("could not record repair for %s", pid)
        return None


def list_repairs(data_dir: Path, pid: str) -> list[dict]:
    f = _file(data_dir, pid)
    if not f.exists():
        return []
    try:
        return list(reversed(json.loads(f.read_text())))
    except ValueError:
        return []


def get_repair(data_dir: Path, pid: str, n: int) -> dict | None:
    return next((r for r in list_repairs(data_dir, pid) if r["n"] == n), None)


def page(data_dir: Path, pid: str, n: int, side: str) -> str | None:
    """Rendered, highlighted, inert HTML of one side of one repair."""
    r = get_repair(data_dir, pid, n)
    if r is None or side not in ("before", "after"):
        return None
    me, other = r[side], r["after" if side == "before" else "before"]
    html = _read_html(data_dir, me["ref"])
    if html is None:
        return None
    sel = _html_items(me.get("item"), me.get("source"))
    return render(html, other_html=_read_html(data_dir, other["ref"]) or "", item_selector=sel,
                  side=side, base_url=me.get("url"))[0]
