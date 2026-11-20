"""Chaos Lab: fake procurement portals that we can break on purpose (spec §6.1).

Portals: ssr, spa, multistep, summary-redirect, login, captcha-open-docs.
Mutations change the markup the way a real portal relaunch would; ground truth follows them.
"""

import html
import random
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .generator import (BERLIN, NATURE_LABEL, PROC_LABEL, LabTender, make_tenders, truth, zip_bytes)

SLUGS = ["ssr", "spa", "multistep", "summary-redirect", "login", "captcha-open-docs"]
PAGE = 10
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
          "Oktober", "November", "Dezember"]


class LabState:
    def __init__(self):
        self.reset()

    def reset(self, slug: str | None = None) -> None:
        if slug is None:
            self.mutations: dict[str, set[str]] = {}
            self.withdrawn: dict[str, set[str]] = {}
            self.passed: dict[str, set[str]] = {}
        else:
            for d in (self.mutations, self.withdrawn, self.passed):
                d.pop(slug, None)

    def has(self, slug: str, m: str) -> bool:
        return m in self.mutations.get(slug, set())

    def mutate(self, slug: str, name: str) -> None:
        from .mutations import apply

        apply(self, slug, name)

    def deadline_for(self, slug: str, t: LabTender) -> datetime:
        if t.id in self.passed.get(slug, set()):
            return datetime(2026, 11, 1, 12, 0, tzinfo=BERLIN)
        return t.deadline


STATE = LabState()


def tenders(slug: str) -> list[LabTender]:
    return make_tenders(slug)


# --- formatting helpers (mutations change these) ---------------------------
def fmt_deadline(slug: str, d: datetime) -> str:
    if STATE.has(slug, "date_format"):
        return f"{d.day}. {MONTHS[d.month - 1]} {d.year}, {d:%H:%M} Uhr"
    return d.strftime("%d.%m.%Y %H:%M")


def fmt_date(slug: str, d) -> str:
    if STATE.has(slug, "date_format"):
        return f"{d.day}. {MONTHS[d.month - 1]} {d.year}"
    return d.strftime("%d.%m.%Y")


def cls(slug: str, name: str) -> str:
    return f"vx-{name[::-1]}" if STATE.has(slug, "rename_classes") else name


def page_shell(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='de'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            "<meta name='generator' content='LabVergabe 3.2'></head><body>"
            "<header><a href='#'>Startseite</a> · <a href='#'>Hilfe</a> · <a href='#'>Impressum</a></header>"
            f"<main>{body}</main><footer>Powered by LabVergabe · Datenschutz · Kontakt</footer></body></html>")


def break_portal(slug: str) -> str:
    """Apply one random repairable mutation that fits this kind of portal; returns its name."""
    from .mutations import REPAIRABLE

    options = [m for m in REPAIRABLE if not STATE.has(slug, m)
               and not (slug == "spa" and m in ("rename_classes", "reshuffle_layout", "pagination_style", "html_to_json"))
               and not (slug != "spa" and m in ("api_path_move", "json_keys_rename"))]
    name = random.choice(options) if options else "date_format"
    STATE.mutate(slug, name)
    return name


def _status_note(slug: str, t: LabTender) -> str:
    return " <strong>(aufgehoben)</strong>" if t.id in STATE.withdrawn.get(slug, set()) else ""


# --- list pages -----------------------------------------------------------
def render_list(slug: str, prefix: str, items: list[LabTender], page: int, total: int, req: Request) -> str:
    rows = []
    for t in items:
        link = f"{prefix}/{slug}/tender/{t.id}"
        d = fmt_deadline(slug, STATE.deadline_for(slug, t))
        if STATE.has(slug, "reshuffle_layout"):
            rows.append(f"<div class='{cls(slug, 'tender-row')}'><h3><a href='{link}'>{html.escape(t.title)}</a>"
                        f"{_status_note(slug, t)}</h3><p>Frist: <span class='{cls(slug, 'deadline')}'>{d}</span></p>"
                        f"<p class='{cls(slug, 'buyer')}'>{html.escape(t.buyer)}</p><small>Nr. {t.id}</small></div>")
        else:
            rows.append(f"<tr class=\"{cls(slug, 'tender-row')}\"><td class='{cls(slug, 'nr')}'>{t.id}</td>"
                        f"<td><a href='{link}'>{html.escape(t.title)}</a>{_status_note(slug, t)}</td>"
                        f"<td class='{cls(slug, 'buyer')}'>{html.escape(t.buyer)}</td>"
                        f"<td class='{cls(slug, 'published')}'>{fmt_date(slug, t.published)}</td>"
                        f"<td class='{cls(slug, 'deadline')}'>{d}</td></tr>")
    pages = (total + PAGE - 1) // PAGE
    if STATE.has(slug, "pagination_style"):
        off = (page - 1) * PAGE
        nav = (f"<a rel='next' class='weiter' href='{prefix}/{slug}/?offset={off + PAGE}'>Weiter »</a>"
               if off + PAGE < total else "")
    else:
        nav = " ".join(f"<a href='{prefix}/{slug}/?page={p}'>{p}</a>" for p in range(1, pages + 1))
    if STATE.has(slug, "reshuffle_layout"):
        listing = f"<section class='{cls(slug, 'results')}'>{''.join(rows)}</section>"
    else:
        listing = (f"<table class='{cls(slug, 'results')}'><thead><tr><th>Nr.</th><th>Titel</th><th>Vergabestelle</th>"
                   f"<th>Veröffentlicht</th><th>Angebotsfrist</th></tr></thead><tbody>{''.join(rows)}</tbody></table>")
    return page_shell("Aktuelle Ausschreibungen",
                      f"<h1>Aktuelle Ausschreibungen</h1><p>{total} Treffer</p>{listing}<nav class='pager'>{nav}</nav>")


def render_detail(slug: str, prefix: str, t: LabTender) -> str:
    d = STATE.deadline_for(slug, t)
    if slug == "multistep":
        docs = f"<p><a class='{cls(slug, 'tab')}' href='{prefix}/{slug}/tender/{t.id}/unterlagen'>Vergabeunterlagen</a></p>"
    else:
        docs = "<ul class='%s'>%s</ul>" % (cls(slug, "documents"), "".join(
            f"<li><a href='{prefix}/{slug}/doc/{t.id}/{n}.pdf'>{html.escape(doc.file_name)}</a></li>"
            for n, doc in enumerate(t.docs, 1)))
    rows = [("Vergabenummer", t.reference), ("Titel", t.title), ("Auftraggeber", t.buyer),
            ("Veröffentlicht am", fmt_date(slug, t.published)), ("Angebotsfrist", fmt_deadline(slug, d)),
            ("Verfahrensart", PROC_LABEL[t.procedure]), ("Art des Auftrags", NATURE_LABEL[t.nature]),
            ("Erfüllungsort", f"{t.place[0]} {t.place[1]}, Deutschland")]
    if t.cpv:
        rows.append(("CPV-Codes", ", ".join(f"{c}-{int(c) % 9}" for c in t.cpv)))
    if t.description:
        rows.append(("Beschreibung", t.description))
    if t.id in STATE.withdrawn.get(slug, set()):
        rows.append(("Status", "Aufgehoben"))
    dl = "".join(f"<dt>{k}</dt><dd class='{cls(slug, 'v')}'>{html.escape(v)}</dd>" for k, v in rows)
    return page_shell(t.title, f"<h1>{html.escape(t.title)}</h1><p>Interne Nr. {t.id}</p>"
                               f"<dl class='{cls(slug, 'details')}'>{dl}</dl><h2>Unterlagen</h2>{docs}")


def api_item(slug: str, prefix: str, t: LabTender) -> dict:
    d = STATE.deadline_for(slug, t)
    item = {"id": t.id, "reference": t.reference, "title": t.title, "authority": t.buyer,
            "publishedAt": t.published.isoformat(), "deadline": d.strftime("%Y-%m-%dT%H:%M:%S"),
            "procedure": PROC_LABEL[t.procedure], "type": NATURE_LABEL[t.nature],
            "cpv": [f"{c}-{int(c) % 9}" for c in t.cpv], "postalCode": t.place[0], "city": t.place[1],
            "country": "DE", "summary": t.description,
            "cancelled": t.id in STATE.withdrawn.get(slug, set()),
            "files": [{"name": doc.file_name, "href": f"{prefix}/{slug}/doc/{t.id}/{n}.pdf"}
                      for n, doc in enumerate(t.docs, 1)]}
    if STATE.has(slug, "json_keys_rename"):
        item = {{"title": "bezeichnung", "authority": "vergabestelle", "deadline": "frist",
                 "publishedAt": "veroeffentlicht", "files": "dokumente"}.get(k, k): v for k, v in item.items()}
    return item


def spa_shell(slug: str, prefix: str) -> str:
    api = f"{prefix}/{slug}/{'api/v2/items' if STATE.has(slug, 'api_path_move') else 'api/tenders'}"
    return ("<!doctype html><html><head><meta charset='utf-8'><title>eVergabe Portal</title></head>"
            "<body><div id='app'>Lädt…</div><script>"
            f"const API='{api}';let off=0;"
            "async function load(){const r=await fetch(API+'?offset='+off+'&limit=10');const j=await r.json();"
            "const el=document.getElementById('app');el.innerHTML='<h1>Ausschreibungen</h1>'+"
            "j.items.map(t=>`<div class=card><a href='"
            f"{prefix}/{slug}/tender/${{t.id}}'>${{t.title||t.bezeichnung}}</a> – ${{t.authority||t.vergabestelle}}"
            " – Frist ${t.deadline||t.frist}</div>`).join('')+"
            "(off+10<j.total?'<button id=more>Mehr laden</button>':'');"
            "const b=document.getElementById('more');if(b)b.onclick=()=>{off+=10;load();};}"
            "load();</script></body></html>")


def create_lab_app() -> FastAPI:
    lab = FastAPI(title="Chaos Lab")

    def prefix(req: Request) -> str:
        return req.scope.get("root_path", "").rstrip("/")

    def check(slug: str) -> None:
        if slug not in SLUGS:
            raise HTTPException(404)
        if STATE.has(slug, "site_down"):
            raise HTTPException(503, "Wartungsarbeiten")

    def find(slug: str, tid: str) -> LabTender:
        for t in tenders(slug):
            if t.id == tid:
                return t
        raise HTTPException(404)

    @lab.get("/", response_class=HTMLResponse)
    async def index(req: Request):
        p = prefix(req)
        return page_shell("Chaos Lab", "<h1>Chaos Lab</h1><ul>" + "".join(
            f"<li><a href='{p}/{s}/'>{s}</a></li>" for s in SLUGS) + "</ul>")

    @lab.get("/_hops/{n}")
    async def hops(n: int, req: Request):
        """A redirect chain, like a cookie check that bounces between load-balanced backends."""
        if n <= 0:
            return HTMLResponse("<p>arrived</p>")
        return RedirectResponse(f"{prefix(req)}/_hops/{n - 1}", status_code=302)

    @lab.get("/{slug}/", response_class=HTMLResponse)
    async def listing(slug: str, req: Request, page: int = 1, offset: int | None = None):
        check(slug)
        p = prefix(req)
        if slug == "summary-redirect":
            t = tenders("ssr")[0]
            return page_shell("Bekanntmachung – TED", (
                f"<h1>Bekanntmachung: {html.escape(t.title)}</h1><p>Auftraggeber: {t.buyer}</p>"
                "<p>Art des Verfahrens: Offenes Verfahren</p>"
                "<p><a href='https://europa.eu/'>Über die EU</a> · <a href='#print'>Drucken</a></p>"
                f"<p>Die Auftragsunterlagen stehen für einen uneingeschränkten und vollständigen direkten Zugang "
                f"gebührenfrei zur Verfügung unter: <a href='{p}/ssr/'>{p}/ssr/</a></p>"))
        if slug == "login" or STATE.has(slug, "add_login"):
            return page_shell("Anmeldung", "<h1>Bitte melden Sie sich an</h1><form method='post' action='login'>"
                                           "<input name='user'><input type=\"password\" name=\"pw\">"
                                           "<button>Anmelden</button></form>")
        if slug == "spa" or STATE.has(slug, "html_to_json"):
            return spa_shell(slug, p)
        items = tenders(slug)
        if offset is not None:
            page = offset // PAGE + 1
        chunk = items[(page - 1) * PAGE: page * PAGE]
        body = render_list(slug, p, chunk, page, len(items), req)
        if slug == "captcha-open-docs":
            body = body.replace("<main>", "<main><div class='captcha-modal'><h2>Sicherheitsabfrage</h2>"
                                          "<p>Bitte bestätigen Sie, dass Sie kein Roboter sind.</p>"
                                          "<img src='captcha.png' alt='captcha'><input name='captcha'></div>", 1)
        return body

    @lab.get("/{slug}/tender/{tid}", response_class=HTMLResponse)
    async def detail(slug: str, tid: str, req: Request):
        check(slug)
        if slug == "login" or STATE.has(slug, "add_login"):
            raise HTTPException(401)
        if slug == "spa" or STATE.has(slug, "html_to_json"):
            return spa_shell(slug, prefix(req))
        return render_detail(slug, prefix(req), find(slug, tid))

    @lab.get("/{slug}/tender/{tid}/unterlagen", response_class=HTMLResponse)
    async def unterlagen(slug: str, tid: str, req: Request):
        check(slug)
        t = find(slug, tid)
        p = prefix(req)
        return page_shell("Vergabeunterlagen", f"<h1>Vergabeunterlagen {t.id}</h1>"
                                               f"<p>Bitte laden Sie die Unterlagen herunter:</p>"
                                               f"<a class='download' href='{p}/{slug}/zip/{t.id}.zip'>Alle Unterlagen (ZIP)</a>")

    @lab.get("/{slug}/zip/{tid}.zip")
    async def zipfile_(slug: str, tid: str):
        check(slug)
        return Response(zip_bytes(find(slug, tid)), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="Unterlagen_{tid}.zip"'})

    @lab.get("/{slug}/doc/{tid}/{n}.pdf")
    async def doc(slug: str, tid: str, n: int):
        check(slug)
        if slug == "login":
            raise HTTPException(401)
        t = find(slug, tid)
        if not 1 <= n <= len(t.docs):
            raise HTTPException(404)
        d = t.docs[n - 1]
        return Response(d.content, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{d.file_name}"'})

    def _api_list(slug: str, req: Request, offset: int, limit: int):
        check(slug)
        items = tenders(slug)
        return {"total": len(items), "items": [api_item(slug, prefix(req), t) for t in items[offset: offset + limit]]}

    @lab.get("/{slug}/api/tenders")
    async def api_list(slug: str, req: Request, offset: int = 0, limit: int = 10):
        if STATE.has(slug, "api_path_move"):
            raise HTTPException(404)
        return _api_list(slug, req, offset, min(limit, 50))

    @lab.get("/{slug}/api/v2/items")
    async def api_list_v2(slug: str, req: Request, offset: int = 0, limit: int = 10):
        if not STATE.has(slug, "api_path_move"):
            raise HTTPException(404)
        return _api_list(slug, req, offset, min(limit, 50))

    @lab.get("/{slug}/api/tenders/{tid}")
    async def api_one(slug: str, tid: str, req: Request):
        check(slug)
        return api_item(slug, prefix(req), find(slug, tid))

    class MutateIn(BaseModel):
        name: str

    @lab.post("/{slug}/mutate")
    async def mutate(slug: str, body: MutateIn):
        if slug not in SLUGS:
            raise HTTPException(404)
        STATE.mutate(slug, body.name)
        return {"mutation": body.name}

    @lab.post("/{slug}/break")
    async def break_it(slug: str):
        from .mutations import REPAIRABLE

        if slug not in SLUGS:
            raise HTTPException(404)
        return {"mutation": break_portal(slug)}

    @lab.post("/{slug}/reset")
    async def reset(slug: str):
        STATE.reset(slug)
        return {"ok": True}

    @lab.get("/{slug}/truth")
    async def get_truth(slug: str, req: Request):
        base = str(req.base_url).rstrip("/")
        base = base[: -len(prefix(req))] if prefix(req) and base.endswith(prefix(req)) else base
        from ..models import tender_json

        return JSONResponse([tender_json(t) for t in truth(slug, base)])

    return lab
