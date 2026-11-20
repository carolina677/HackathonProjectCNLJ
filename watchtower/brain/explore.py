"""Explore an unseen portal like a person would: what page is this, where do I click, am I blocked?"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from .. import events
from ..fetch import CapturedJson, Fetcher, Link, Page, PortalCancelled, RenderedPage, extract_links
from ..judge.jev import JudgeUnavailable
from ..recipe.engine import _form_data, visible_text

log = logging.getLogger("watchtower.explore")

PAGE_TYPES = {
    "tender_list": "A page listing several public tenders / contract notices (a table or list of calls for tender).",
    "tender_detail": "A page about one single tender with its details (buyer, deadline, documents).",
    "notice_summary": "A notice or summary (e.g. TED, national gazette) that points to another website where the tender documents are.",
    "search_form": "A search form for tenders with no results listed yet.",
    "login_wall": "A page that requires logging in or registering before tenders or documents are shown.",
    "captcha": "A page showing a captcha or bot check.",
    "error": "An error, maintenance or not-found page.",
    "other": "Anything else (home page, news, help, imprint).",
}
GOALS = {
    "open_list": "the list of currently open tenders / calls for tender",
    "next_page": "the next page of the tender list",
    "detail": "the detail page of one individual tender",
    "documents": "the tender documents (a file download, or a tab/page with the documents)",
    "platform": "the procurement platform or website where the tender documents are published",
}
NAV_WORDS = {"startseite", "home", "hilfe", "help", "impressum", "datenschutz", "privacy", "kontakt", "contact",
             "login", "anmelden", "registrieren", "faq", "sitemap", "accessibility", "barrierefreiheit"}
FILE_EXT = re.compile(r"\.(pdf|zip|docx?|xlsx?|odt|rtf|7z|gaeb|x8\d)(\?|$)", re.I)
DOC_WORDS = ("unterlagen", "dokument", "download", "document", "pliego", "dce", "bestand", "anlagen", "files")


@dataclass
class Exploration:
    list_page: Page | None = None
    detail_pages: list[Page] = field(default_factory=list)
    json_candidates: list[CapturedJson] = field(default_factory=list)
    blocked: Literal["login", "captcha", "firewall"] | None = None
    docs_reachable: bool = False
    doc_hops: list[str] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    error: str | None = None


def _title(html: str) -> str:
    t = LexborHTMLParser(html).css_first("title")
    return t.text(strip=True) if t else ""


def _forms(html: str) -> list[str]:
    tree = LexborHTMLParser(html)
    return [f"{i.attributes.get('type', 'text')}:{i.attributes.get('name', '')}" for i in tree.css("form input")][:20]


async def classify_page(page: Page, jev, portal_id: str | None = None):
    state = {"url": page.final_url, "title": _title(page.html), "text": visible_text(page.html, 6000),
             "forms": _forms(page.html)}
    return await jev.choice(state, "What kind of page is this, for someone collecting public tenders?",
                            PAGE_TYPES, portal_id=portal_id, tag="page_type")


def _links(page: Page) -> list[Link]:
    return page.links if isinstance(page, RenderedPage) and page.links else extract_links(page.html, page.final_url)


def _candidates(page: Page, goal: str) -> list[Link]:
    host = urlsplit(page.final_url).hostname
    out = []
    for l in _links(page):
        same = urlsplit(l.href).hostname == host
        text = l.text.lower().strip()
        if l.href.rstrip("/") == page.final_url.rstrip("/"):
            continue
        if goal in ("detail", "open_list", "next_page") and not same:
            continue
        if goal == "detail" and (FILE_EXT.search(l.href) or text in NAV_WORDS or text.isdigit()):
            continue
        out.append(l)
    if goal == "documents":
        out.sort(key=lambda l: (not FILE_EXT.search(l.href), not any(w in l.text.lower() for w in DOC_WORDS)))
    elif goal == "detail":
        out.sort(key=lambda l: (not re.search(r"\d", l.href), -len(l.text)))
    return out[:40]


async def rank_links(page: Page, goal: str, jev, k: int = 5, portal_id: str | None = None,
                     threshold: float = 0.5) -> list[tuple[Link, float]]:
    cands = _candidates(page, goal)
    if not cands:
        return []
    state = {"page_url": page.final_url, "page_title": _title(page.html), "goal": f"{goal}: {GOALS[goal]}",
             "links": [{"text": l.text, "href": l.href, "context": l.context} for l in cands]}
    qs = {f"link_{i}": f"Does link #{i} (text '{l.text}', href {l.href}) lead to {GOALS[goal]}?"
          for i, l in enumerate(cands)}
    probs = await jev.nouls(state, qs, portal_id=portal_id, tag=f"links:{goal}")
    ranked = sorted(((cands[int(q.split('_')[1])], p) for q, p in probs.items()), key=lambda x: -x[1])
    events.emit(portal_id, "links", {"goal": goal, "top": [{"href": l.href, "text": l.text, "p": round(p, 3)}
                                                           for l, p in ranked[:k]]})
    return [(l, p) for l, p in ranked[:k] if p >= threshold]


def _list_like(body: Any, depth: int = 0) -> list | None:
    if isinstance(body, list) and len(body) >= 2 and all(isinstance(x, dict) for x in body[:5]):
        if sum(isinstance(v, str) for v in body[0].values()) >= 2:
            return body
    if isinstance(body, dict) and depth < 3:
        for v in body.values():
            r = _list_like(v, depth + 1)
            if r:
                return r
    return None


def json_tender_candidates(page: Page) -> list[CapturedJson]:
    if not isinstance(page, RenderedPage):
        return []
    hits = [(c, _list_like(c.body)) for c in page.json_responses]
    hits = [(c, l) for c, l in hits if l]
    hits.sort(key=lambda x: -len(x[1]))
    return [c for c, _ in hits]


async def _probe_download(fetcher: Fetcher, url: str) -> Literal["ok", "auth", "html", "other"]:
    try:
        async with fetcher.stream(url) as r:
            if r.status_code in (401, 403):
                return "auth"
            if r.status_code >= 400:
                return "other"
            ct = r.headers.get("content-type", "").lower()
            async for chunk in r.aiter_bytes():
                head = chunk[:16]
                break
            else:
                head = b""
        if (head.startswith(b"%PDF") or head.startswith(b"PK") or "pdf" in ct or "zip" in ct
                or "octet-stream" in ct or "msword" in ct or "officedocument" in ct):
            return "ok"
        if "html" in ct or head.lstrip().startswith(b"<"):
            return "html"
        return "other"
    except PortalCancelled:
        raise
    except Exception as e:
        log.info("probe %s failed: %s", url, e)
        return "other"


async def _docs_reachable(detail: Page, fetcher: Fetcher, jev, portal_id,
                          ex: Exploration) -> tuple[bool, Literal["login", "captcha"] | None]:
    doc_block: Literal["login", "captcha"] | None = None
    for link, _ in await rank_links(detail, "documents", jev, k=3, portal_id=portal_id):
        res = await _probe_download(fetcher, link.href)
        if res == "ok":
            ex.doc_hops = [link.href]
            return True, None
        if res == "auth":
            doc_block = doc_block or "login"
            continue
        if res == "other" and (FILE_EXT.search(link.href) or "download" in link.href.lower()):
            continue
        tab = await fetcher.open(link.href)
        ex.path.append(link.href)
        if tab.status in (401, 403):
            doc_block = doc_block or "login"
            continue
        if tab.status < 400:
            tkind = (await classify_page(tab, jev, portal_id)).choice
            if tkind == "captcha":
                doc_block = "captcha"
            elif tkind == "login_wall":
                doc_block = doc_block or "login"
            for inner, _ in await rank_links(tab, "documents", jev, k=3, portal_id=portal_id):
                ires = await _probe_download(fetcher, inner.href)
                if ires == "ok":
                    ex.doc_hops = [link.href, inner.href]
                    return True, None
                if ires == "auth":
                    doc_block = doc_block or "login"
    return False, doc_block


def _find_search_form(html: str, base: str) -> tuple[str, str, dict[str, str]] | None:
    tree = LexborHTMLParser(html)
    for form in tree.css("form"):
        inputs = form.css("input")
        if any((i.attributes.get("type") or "").lower() == "password" for i in inputs):
            continue
        action = urljoin(base, form.attributes.get("action") or base)
        method = (form.attributes.get("method") or "get").lower()
        return method, action, _form_data(form)
    return None


# Block pages of web application firewalls (F5, Akamai, Imperva, Cloudflare). They reject automated
# requests on purpose; Watchtower reports that and never tries to get around it.
FIREWALL = re.compile(r"The requested URL was rejected\. Please consult with your administrator"
                      r"|<title>\s*Access Denied\s*</title>[\s\S]{0,400}Reference #\d"
                      r"|Incapsula incident ID|Attention Required! \| Cloudflare|cf-error-details", re.I)


async def explore(start_url: str, fetcher: Fetcher, jev, *, portal_id: str | None = None,
                  max_hops: int = 4, max_details: int = 3) -> Exploration:
    ex = Exploration()
    cur, hops, block_seen, form_tried = start_url, 0, None, False
    next_page: Page | None = None
    try:
        while hops <= max_hops:
            page = next_page if next_page is not None else await fetcher.open(cur)
            next_page = None
            ex.path.append(cur)
            if page.status in (401, 403):
                ex.blocked = "login"
                return ex
            if FIREWALL.search(page.html[:20000]):
                ex.blocked = "firewall"
                return ex
            if page.status >= 400:
                ex.error = f"HTTP {page.status} on {cur}"
                return ex
            kind = (await classify_page(page, jev, portal_id)).choice
            if kind == "tender_list":
                ex.list_page = page
                break
            if kind in ("captcha", "login_wall"):
                if await rank_links(page, "detail", jev, portal_id=portal_id):
                    ex.list_page, block_seen = page, kind
                    break
                ex.blocked = "captcha" if kind == "captcha" else "login"
                return ex
            goal = "platform" if kind == "notice_summary" else "open_list"
            nxt = await rank_links(page, goal, jev, k=3, portal_id=portal_id)
            if kind == "tender_detail" and not nxt:
                ex.list_page = page
                break
            if not nxt and kind == "search_form" and not form_tried:
                form = _find_search_form(page.html, page.final_url or cur)
                if form:
                    form_tried = True
                    method, action, data = form
                    if method == "post":
                        resp = await fetcher.post(action, data)
                    else:
                        u = urlsplit(action)
                        merged = parse_qsl(u.query, keep_blank_values=True) + list(data.items())
                        resp = await fetcher.open(urlunsplit((u.scheme, u.netloc, u.path, urlencode(merged), "")))
                    next_page, cur, hops = resp, resp.final_url or action, hops + 1
                    continue
            if not nxt:
                ex.error = f"no way to a tender list from {cur} ({kind})"
                return ex
            cur, hops = nxt[0][0].href, hops + 1
        if ex.list_page is None:
            ex.error = ex.error or "gave up looking for the tender list"
            return ex

        ex.json_candidates = json_tender_candidates(ex.list_page)
        detail_block: Literal["login", "captcha"] | None = None
        blocked_details: list[Page] = []
        for link, _ in await rank_links(ex.list_page, "detail", jev, k=max_details, portal_id=portal_id):
            d = await fetcher.open(link.href)
            if d.status in (401, 403):
                detail_block = detail_block or "login"
                continue
            if d.status < 400:
                dkind = (await classify_page(d, jev, portal_id)).choice
                if dkind == "captcha":
                    detail_block = "captcha"
                    blocked_details.append(d)
                elif dkind == "login_wall":
                    detail_block = detail_block or "login"
                    blocked_details.append(d)
                else:
                    ex.detail_pages.append(d)
                    ex.path.append(link.href)
        doc_block: Literal["login", "captcha"] | None = None
        for d in (ex.detail_pages or blocked_details)[:1]:
            ex.docs_reachable, doc_block = await _docs_reachable(d, fetcher, jev, portal_id, ex)
        if not ex.docs_reachable:
            if block_seen:
                ex.blocked = "captcha" if block_seen == "captcha" else "login"
            elif doc_block:
                ex.blocked = doc_block
            elif detail_block and not ex.detail_pages:
                ex.blocked = detail_block
    except JudgeUnavailable as e:
        ex.error = f"no judge available: {e}"
    events.emit(portal_id, "explore", {"path": ex.path, "blocked": ex.blocked, "docs_reachable": ex.docs_reachable,
                                       "json_apis": [c.url for c in ex.json_candidates][:3], "error": ex.error})
    return ex
