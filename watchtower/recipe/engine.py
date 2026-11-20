"""Deterministic recipe runner. Regular reads go through here and never call a model."""

import re
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from jsonpath_ng.ext import parse as jp_parse
from lxml import html as lxml_html
from selectolax.lexbor import LexborHTMLParser, LexborNode

from ..models import OPTIONAL_FIELDS, TenderStatus
from . import normalize as N
from .dsl import DocStep, FieldRule, Recipe

RawTender = dict[str, Any]
LABEL_TAGS = "dt, th, td, label, strong, b, span, div, p, li"


class FetcherLike(Protocol):
    async def get(self, url: str): ...
    async def get_json(self, url: str, params: dict | None = None): ...


def visible_text(html: str, limit: int | None = None) -> str:
    tree = LexborHTMLParser(html)
    for n in tree.css("script, style, noscript, template"):
        n.decompose()
    root = tree.body or tree.root
    txt = re.sub(r"\s+", " ", root.text(separator=" ") if root else "").strip()
    return txt[:limit] if limit else txt


def with_param(url: str, key: str, value) -> str:
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != key] + [(key, str(value))]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


# --- html selection --------------------------------------------------------
def _node_value(node: LexborNode, attr: str) -> str | None:
    if attr == "text":
        return node.text(separator=" ", strip=True)
    return node.attributes.get(attr)


def _next_element(node: LexborNode) -> LexborNode | None:
    n = node.next
    while n is not None and n.tag in ("-text", "-comment"):
        n = n.next
    return n


def find_by_label(root: LexborNode, label: str) -> str | None:
    want = label.lower().rstrip(":").strip()
    for n in root.css(LABEL_TAGS):
        own = (n.text(deep=False, strip=True) or n.text(strip=True) or "").lower().rstrip(":").strip()
        # A label may carry a short suffix ("Frist *"), but never more letters ("Vergabestellen").
        if own == want or (own.startswith(want) and len(own) <= len(want) + 3 and not own[len(want)].isalpha()):
            sib = _next_element(n)
            if sib is not None:
                v = sib.text(separator=" ", strip=True)
                if v:
                    return v
            rest = n.text(strip=True)[len(label):].strip(" :")
            if rest:
                return rest
    return None


def select_html(root: LexborNode, rule: FieldRule) -> list[str]:
    if rule.label:
        v = find_by_label(root, rule.label)
        return [v] if v else []
    if not rule.selector:
        return []
    if rule.selector in (".", ":self"):
        v = _node_value(root, rule.attr)
        return [v] if v else []
    if rule.selector.startswith("/") or rule.selector.startswith("./"):
        doc = lxml_html.fromstring(root.html or "<div/>")
        out = []
        for r in doc.xpath(rule.selector):
            if isinstance(r, str):
                out.append(r.strip())
            elif rule.attr == "text":
                out.append(re.sub(r"\s+", " ", r.text_content()).strip())
            else:
                out.append(r.get(rule.attr))
        return [o for o in out if o]
    return [v for v in (_node_value(n, rule.attr) for n in root.css(rule.selector)) if v]


def select_json(obj: Any, path: str) -> list[Any]:
    return [m.value for m in jp_parse(path).find(obj)]


# --- normalisation ---------------------------------------------------------
def _regex(v: str, rx: str | None) -> str | None:
    if not rx or v is None:
        return v
    m = re.search(rx, str(v))
    if not m:
        return None
    return m.group(1) if m.groups() else m.group(0)


def apply_rule(values: list, rule: FieldRule, country: str | None, base_url: str):
    if rule.normalizer == "cpv_list":
        flat = [_regex(str(v), rule.regex) for v in values]
        codes = N.parse_cpv([f for f in flat if f])
        return codes or None
    if not values:
        return None
    v = values[0]
    if isinstance(v, bool) and rule.normalizer == "status":
        return N.map_status(v)
    v = _regex(v if isinstance(v, str) else str(v), rule.regex) if not isinstance(v, (int, float)) or rule.regex else v
    if v is None:
        return None
    if rule.template:
        v = rule.template.format(value=v)
    fn = {
        "text": N.clean_text, "date": lambda s: N.parse_date(s, country),
        "datetime": lambda s: N.parse_deadline(s, country), "country": N.parse_country,
        "procedure": N.map_procedure, "nature": N.map_nature, "status": N.map_status,
        "url": lambda s: urljoin(base_url, str(s).strip()), "int": N.parse_int,
    }[rule.normalizer]
    return fn(v)


def _set(t: RawTender, key: str, value) -> None:
    if "." in key:
        head, sub = key.split(".", 1)
        if value is not None:
            t.setdefault(head, {})[sub] = value
    elif value is not None or key not in t:
        t[key] = value


def _extract(fields: dict[str, FieldRule], selector, country, base_url, into: RawTender) -> None:
    for key, rule in fields.items():
        try:
            vals = selector(rule)
        except Exception:
            vals = []
        val = apply_rule(vals, rule, country, base_url)
        if val is not None or key not in into:
            _set(into, key, val)


def resolve_param(v):
    """Runtime placeholders in extra_params, e.g. {"deadlineFrom": "{today}"}."""
    if isinstance(v, str) and "{today}" in v:
        return v.replace("{today}", datetime.now(timezone.utc).date().isoformat())
    return v


# --- post-back pagination (PRADO, ASP.NET WebForms) --------------------------
STATE_FIELDS = ("PRADO_PAGESTATE", "__VIEWSTATE")
DOPOSTBACK = re.compile(r"__doPostBack\(\s*'([^']*)'\s*,\s*'([^']*)'")


def _form_of(root: LexborNode):
    for name in STATE_FIELDS:
        n = root.css_first(f"input[name='{name}']")
        while n is not None and n.tag != "form":
            n = n.parent
        if n is not None:
            return n
    return root.css_first("form[method='post'], form[method='POST']")


def _form_data(form: LexborNode) -> dict[str, str]:
    data: dict[str, str] = {}
    for i in form.css("input"):
        name, typ = i.attributes.get("name"), (i.attributes.get("type") or "text").lower()
        if not name or typ in ("submit", "button", "image", "reset", "file"):
            continue
        if typ in ("checkbox", "radio") and "checked" not in i.attributes:
            continue
        data[name] = i.attributes.get("value") or ("on" if typ in ("checkbox", "radio") else "")
    for s in form.css("select"):
        name = s.attributes.get("name")
        opt = s.css_first("option[selected]") or s.css_first("option")
        if name and opt is not None:
            data[name] = opt.attributes.get("value") or opt.text(strip=True)
    for t in form.css("textarea"):
        if t.attributes.get("name"):
            data[t.attributes["name"]] = t.text()
    return data


def _client_id_to_name(cid: str, names: list[str]) -> str:
    """PRADO/ASP.NET client ids replace '$' with '_'. Rebuild the unique name from segments the form already uses."""
    segs = sorted({s for n in names for s in n.split("$") if s}, key=len, reverse=True)
    out, rest = [], cid
    while rest:
        seg = next((s for s in segs if rest == s or rest.startswith(s + "_")), None) or rest.split("_", 1)[0]
        out.append(seg)
        rest = rest[len(seg) + 1:]
    return "$".join(out)


def postback_request(html: str, base: str, next_selector: str | None, target: str | None):
    """The form post that a click on "next" would send, or None on the last page."""
    root = LexborHTMLParser(html).root
    nxt = root.css_first(next_selector) if next_selector else None
    if nxt is None or "disabled" in (nxt.attributes.get("class") or "") or "disabled" in nxt.attributes:
        return None
    form = _form_of(root)
    if form is None:
        return None
    data = _form_data(form)
    m = DOPOSTBACK.search(nxt.attributes.get("href") or "") or DOPOSTBACK.search(nxt.attributes.get("onclick") or "")
    arg = ""
    if m:
        target, arg = m.group(1), m.group(2)
    elif not target:
        cid = nxt.attributes.get("id")
        if not cid:
            return None
        target = _client_id_to_name(cid, list(data))
    if "PRADO_PAGESTATE" in data:
        data.update({"PRADO_POSTBACK_TARGET": target, "PRADO_POSTBACK_PARAMETER": arg})
    else:
        data.update({"__EVENTTARGET": target, "__EVENTARGUMENT": arg})
    return urljoin(base, form.attributes.get("action") or base), data


# --- runner ----------------------------------------------------------------
async def _list_pages(recipe: Recipe, fetcher: FetcherLike, errors: list[str]):
    pg = recipe.pagination
    url = recipe.list_url
    for k, v in recipe.extra_params.items():
        url = with_param(url, k, resolve_param(v))
    seen_pages = 0
    next_url: str | None = url
    while next_url and seen_pages < pg.max_pages:
        if pg.kind == "page_param":
            page_url = with_param(next_url, pg.param or "page", pg.start + seen_pages * pg.step) \
                if seen_pages or pg.start != 1 else next_url
        elif pg.kind == "offset":
            page_url = with_param(next_url, pg.param or "offset", pg.start + seen_pages * pg.step)
        else:
            page_url = next_url
        if recipe.source == "json_api":
            try:
                data = await fetcher.get_json(page_url)
            except Exception as e:
                errors.append(f"list request failed on {page_url}: {e}")
                return
            yield page_url, data
            next_href = None
        else:
            page = await fetcher.get(page_url)
            if page.status >= 400:
                errors.append(f"HTTP {page.status} on {page_url}")
                return
            yield page.final_url or page_url, page.html
            next_href = None
            if pg.kind == "postback":
                pages_seen = 1
                while pages_seen < pg.max_pages:
                    req = postback_request(page.html, page.final_url or page_url, pg.next_selector, pg.param)
                    if req is None:
                        return
                    action, data = req
                    try:
                        page = await fetcher.post(action, data)
                    except Exception as e:
                        errors.append(f"post-back failed on {action}: {e}")
                        return
                    if page.status >= 400:
                        errors.append(f"HTTP {page.status} on {action}")
                        return
                    pages_seen += 1
                    yield page.final_url or action, page.html
                if postback_request(page.html, page.final_url or page_url, pg.next_selector, pg.param):
                    errors.append(f"{LIST_INCOMPLETE}: stopped after max_pages={pg.max_pages}")
                return
            if pg.kind == "next_link" and pg.next_selector:
                n = LexborHTMLParser(page.html).css_first(pg.next_selector)
                next_href = urljoin(page.final_url or page_url, n.attributes.get("href")) if n and n.attributes.get("href") else None
        seen_pages += 1
        if pg.kind == "none":
            return
        if pg.kind == "next_link":
            next_url = next_href
        else:
            next_url = url
    if next_url and seen_pages >= pg.max_pages:
        errors.append(f"{LIST_INCOMPLETE}: stopped after max_pages={pg.max_pages}")


LIST_INCOMPLETE = "list incomplete"
_LIST_ERRORS = ("HTTP ", "list request failed", "post-back failed", LIST_INCOMPLETE)


def list_complete(errors: list[str]) -> bool:
    """False when the list pages were not all read, so missing tenders must not be taken as gone."""
    return not any(e.startswith(_LIST_ERRORS) for e in errors)


async def run_recipe(recipe: Recipe, fetcher: FetcherLike, *, limit: int | None = None,
                     errors: list[str] | None = None, mask: set[str] | None = None,
                     pages: dict[str, str] | None = None, on_list=None, on_tender=None) -> list[RawTender]:
    """Run a recipe. `pages` (optional) collects detail-page HTML by tender id for verification.

    on_list(items, errors) runs after the list pages; returning False stops before any detail request.
    on_tender(t) runs as soon as one tender is complete (details, document URLs, mask applied)."""
    errors = errors if errors is not None else []
    mask = mask or set()
    out: list[RawTender] = []
    seen: set[str] = set()
    country = recipe.country
    async for page_url, payload in _list_pages(recipe, fetcher, errors):
        if recipe.source == "json_api":
            items = select_json(payload, recipe.item)
            selector_for = lambda item: (lambda r: select_json(item, r.selector) if r.selector else [])
        else:
            root = LexborHTMLParser(payload).root
            items = root.css(recipe.item) if not recipe.item.startswith("/") else []
            selector_for = lambda item: (lambda r: select_html(item, r))
        if not items:
            if not out:
                errors.append(f"list selector matched no items on {page_url}")
            break
        new = 0
        for item in items:
            t: RawTender = {}
            _extract(recipe.fields, selector_for(item), country, page_url, t)
            tid = t.get(recipe.id_field)
            if not tid:
                errors.append("item without id")
                continue
            if tid in seen:
                continue
            seen.add(tid)
            t["id"] = str(tid)
            if "status" not in recipe.fields and recipe.source == "html" and _withdrawn_marker(item, t):
                t["status"] = TenderStatus.withdrawn
            t["_item"] = item if recipe.source == "json_api" else None
            t["_base"] = page_url
            out.append(t)
            new += 1
            if limit and len(out) >= limit:
                break
        if new == 0 or (limit and len(out) >= limit):
            break

    if on_list is not None and await on_list(out, errors) is False:
        return out
    for t in out:
        await _detail_and_docs(recipe, fetcher, t, errors, pages)
        _finalise(t, mask)
        if on_tender is not None:
            await on_tender(t)
    return out


async def _detail_and_docs(recipe: Recipe, fetcher: FetcherLike, t: RawTender, errors: list[str],
                           pages: dict[str, str] | None) -> None:
    country = recipe.country
    doc_ctx: tuple[str, Any] | None = None  # ("html", (root, base)) or ("json", obj)
    if recipe.detail:
        d = recipe.detail
        url = d.url_template.format(**{k: v for k, v in t.items() if isinstance(v, (str, int))}) \
            if d.url_template else t.get(d.url_field)
        if url:
            try:
                if d.source == "json":
                    obj = await fetcher.get_json(url)
                    _extract(d.fields, lambda r: select_json(obj, r.selector) if r.selector else [], country, url, t)
                    doc_ctx = ("json", obj)
                else:
                    page = await fetcher.get(url)
                    if page.status >= 400:
                        errors.append(f"detail HTTP {page.status} on {url}")
                    else:
                        root = LexborHTMLParser(page.html).root
                        _extract(d.fields, lambda r: select_html(root, r), country, page.final_url or url, t)
                        if t.get("status") is None and "status" not in d.fields:
                            label = find_by_label(root, "Status")
                            if label and N.map_status(label):
                                t["status"] = TenderStatus.withdrawn
                        doc_ctx = ("html", (root, page.final_url or url))
                        if pages is not None:
                            pages[t["id"]] = page.html
            except Exception as e:
                errors.append(f"detail failed for {t['id']}: {e}")
    if doc_ctx is None:
        if recipe.source == "json_api" and t.get("_item") is not None:
            doc_ctx = ("json", t["_item"])
        elif recipe.source == "html":
            doc_ctx = None
    t["_doc_urls"] = await _documents(recipe.documents, fetcher, doc_ctx, t, errors) if recipe.documents else []


async def _documents(steps: list[DocStep], fetcher: FetcherLike, ctx, t: RawTender, errors: list[str]) -> list[str]:
    if ctx is None:
        return []
    kind, obj = ctx
    current: list[tuple[str, Any]] = [(kind, obj)]
    for i, step in enumerate(steps):
        urls: list[str] = []
        for k, o in current:
            if k == "json":
                base = t.get("_base", "")
                vals = select_json(o, step.selector)
                urls += [urljoin(base, str(v)) for v in vals if v]
            else:
                root, base = o
                urls += [urljoin(base, v) for v in (n.attributes.get(step.attr) for n in root.css(step.selector)) if v]
        urls = list(dict.fromkeys(urls))
        if not step.follow or i == len(steps) - 1:
            return urls
        nxt = []
        for u in urls:
            try:
                p = await fetcher.get(u)
                nxt.append(("html", (LexborHTMLParser(p.html).root, p.final_url or u)))
            except Exception as e:
                errors.append(f"document step failed for {t['id']}: {e}")
        current = nxt
    return []


WITHDRAWN_RX = re.compile(r"\b(aufgehoben|zurückgezogen|withdrawn|cancell?ed|annul[ée]e?|ingetrokken|geannuleerd)\b",
                          re.I)


def _withdrawn_marker(item, t: RawTender) -> bool:
    """No status rule in the recipe: a withdrawal marker next to the title still counts (e.g. "(aufgehoben)")."""
    text = item.text(separator=" ")
    title = t.get("title") or ""
    if title:
        text = text.replace(title, " ")
    return bool(WITHDRAWN_RX.search(text))


def _finalise(t: RawTender, mask: set[str]) -> None:
    t.pop("_item", None)
    if t.get("status") is None:
        dl = t.get("submissionDeadline")
        t["status"] = TenderStatus.closed if isinstance(dl, datetime) and dl < datetime.now(timezone.utc) \
            else TenderStatus.open
    for k in mask:
        if k in OPTIONAL_FIELDS:
            t.pop(k, None)
        else:
            t[k] = None
    for k in [k for k in OPTIONAL_FIELDS if t.get(k) in (None, [], {})]:
        t.pop(k, None)
