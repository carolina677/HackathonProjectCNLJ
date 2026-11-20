"""Blind lab: a procurement portal nobody on the team has seen, generated from a seed.

    /blind/{seed}/        list page (HTML, or a JS shell over a JSON API)
    /blind/{seed}/truth   what a perfect system returns

Every seed picks its own layout, class names, column order, label language, date format, pagination,
URL scheme, tender ids and (for JSON portals) API path and keys. Pick the seed at random at run time
(scripts/blind_test.py does) and nobody can have tuned Watchtower on that portal.
"""

import html
import random
import string
from dataclasses import dataclass, field
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ..models import ContractNature, Document, Place, ProcedureType, Tender, TenderStatus, tender_json
from .generator import BERLIN, LabTender, make_tenders

LABELS = {
    "de": dict(heading="Ausschreibungen", ref="Vergabenummer", title="Bezeichnung", buyer="Auftraggeber",
               published="Veröffentlicht", deadline="Angebotsfrist", procedure="Verfahren", nature="Auftragsart",
               place="Ausführungsort", cpv="CPV", description="Kurzbeschreibung", docs="Unterlagen",
               next="Weiter", country="Deutschland", id="Kennung",
               months=["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
                       "Oktober", "November", "Dezember"]),
    "fr": dict(heading="Avis de marchés", ref="Référence", title="Objet", buyer="Acheteur public",
               published="Date de publication", deadline="Date limite de remise des offres", procedure="Procédure",
               nature="Type de marché", place="Lieu d'exécution", cpv="Codes CPV", description="Description",
               docs="Dossier de consultation", next="Suivant", country="Allemagne", id="Identifiant",
               months=["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
                       "octobre", "novembre", "décembre"]),
    "nl": dict(heading="Aanbestedingen", ref="Kenmerk", title="Naam opdracht", buyer="Aanbestedende dienst",
               published="Publicatiedatum", deadline="Sluitingsdatum inschrijving", procedure="Procedure",
               nature="Type opdracht", place="Plaats van uitvoering", cpv="CPV-codes", description="Omschrijving",
               docs="Aanbestedingsstukken", next="Volgende", country="Duitsland", id="ID",
               months=["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
                       "oktober", "november", "december"]),
    "en": dict(heading="Open tenders", ref="Reference number", title="Contract title", buyer="Contracting authority",
               published="Published", deadline="Tender deadline", procedure="Procedure type", nature="Contract type",
               place="Place of performance", cpv="CPV codes", description="Summary", docs="Tender documents",
               next="Next", country="Germany", id="Notice ID",
               months=["January", "February", "March", "April", "May", "June", "July", "August", "September",
                       "October", "November", "December"]),
}
PROC = {
    "de": {ProcedureType.open: "Offenes Verfahren", ProcedureType.restricted: "Nicht offenes Verfahren",
           ProcedureType.negotiated: "Verhandlungsverfahren", ProcedureType.other: "Innovationspartnerschaft"},
    "fr": {ProcedureType.open: "Appel d'offres ouvert", ProcedureType.restricted: "Appel d'offres restreint",
           ProcedureType.negotiated: "Procédure avec négociation", ProcedureType.other: "Partenariat d'innovation"},
    "nl": {ProcedureType.open: "Openbare procedure", ProcedureType.restricted: "Niet-openbare procedure",
           ProcedureType.negotiated: "Mededingingsprocedure met onderhandeling",
           ProcedureType.other: "Innovatiepartnerschap"},
    "en": {ProcedureType.open: "Open procedure", ProcedureType.restricted: "Restricted procedure",
           ProcedureType.negotiated: "Negotiated procedure", ProcedureType.other: "Innovation partnership"},
}
NATURE = {
    "de": {ContractNature.works: "Bauleistung", ContractNature.services: "Dienstleistung",
           ContractNature.supplies: "Lieferung"},
    "fr": {ContractNature.works: "Travaux", ContractNature.services: "Services", ContractNature.supplies: "Fournitures"},
    "nl": {ContractNature.works: "Werken", ContractNature.services: "Diensten", ContractNature.supplies: "Leveringen"},
    "en": {ContractNature.works: "Works", ContractNature.services: "Services", ContractNature.supplies: "Supplies"},
}
FIELDS = ["ref", "title", "buyer", "published", "deadline", "procedure", "nature", "place", "cpv", "description"]
LIST_COLS = ["id", "title", "buyer", "published", "deadline"]
JSON_KEY_POOL = {
    "items": ["results", "data", "notices", "hits", "records"],
    "id": ["uid", "noticeId", "key", "nr", "_id"],
    "ref": ["ref", "dossier", "fileNumber", "kenmerk", "reference_no"],
    "title": ["name", "subject", "objet", "label", "heading"],
    "buyer": ["org", "acheteur", "issuer", "dienst", "client"],
    "published": ["pub", "date_pub", "releasedOn", "posted", "issued"],
    "deadline": ["closes", "date_limite", "dueAt", "sluiting", "endDate"],
    "procedure": ["proc", "procedureLabel", "mode", "kind", "procedureName"],
    "nature": ["category", "typeMarche", "contractType", "soort", "nature_label"],
    "place": ["location", "lieu", "where", "plaats", "site"],
    "cpv": ["cpvList", "codes", "classification", "cpv_codes", "nomenclature"],
    "description": ["text", "resume", "body", "omschrijving", "abstract"],
    "files": ["attachments", "pieces", "downloads", "bijlagen", "files_list"],
    "fname": ["filename", "label", "title", "naam", "nom"],
    "fhref": ["url", "link", "download", "href", "src"],
    "total": ["count", "total", "nbResults", "aantal", "totalHits"],
}


@dataclass(frozen=True)
class BlindSpec:
    seed: int
    layout: str              # table | cards | dl
    source: str              # html | json
    lang: str
    date_fmt: str            # dmy | iso | long | slash
    paging: str              # page | offset | next
    page_size: int
    cpv_check_digit: bool
    detail_seg: str
    file_seg: str
    page_param: str
    css: dict = field(hash=False, compare=True)
    order: tuple = ()
    ids: tuple = ()
    api_path: str = ""
    json_offset: str = "offset"
    json_keys: dict = field(default_factory=dict, hash=False, compare=True)


def _word(rng: random.Random, n: int = 6) -> str:
    return rng.choice(string.ascii_lowercase) + "".join(rng.choice(string.ascii_lowercase + string.digits)
                                                        for _ in range(n - 1))


def blind_spec(seed: int) -> BlindSpec:
    rng = random.Random(f"blind-portal-{seed}")
    lang = rng.choice(list(LABELS))
    paging = rng.choice(["page", "offset", "next"])
    prefix = rng.choice(["", "AZ-", "N", "T-2026-", "DOS", "PB"])
    nums = rng.sample(range(10_000, 99_999), 23)
    return BlindSpec(
        seed=seed, layout=rng.choice(["table", "cards", "dl"]), source=rng.choice(["html", "html", "json"]),
        lang=lang, date_fmt=rng.choice(["dmy", "iso", "long", "slash"]), paging=paging,
        page_size=rng.choice([5, 8, 10, 12]), cpv_check_digit=rng.random() < 0.5,
        detail_seg=rng.choice(["notice", "avis", "aanbesteding", "ausschreibung", "item", "dossier", "t"]),
        file_seg=rng.choice(["download", "fichier", "bestand", "datei", "att"]),
        page_param={"page": rng.choice(["page", "p", "seite", "pagina"]),
                    "offset": rng.choice(["offset", "start", "from", "skip"]), "next": "cursor"}[paging],
        css={k: _word(rng) for k in ["list", "row", "link", "meta", "field", "value", "docs", "pager", *LIST_COLS]},
        order=tuple(["title"] + rng.sample([c for c in LIST_COLS if c != "title"], 4)),
        ids=tuple(f"{prefix}{n}" for n in nums),
        api_path=f"{_word(rng, 4)}/{rng.choice(['v1', 'v3', 'public', 'rest'])}/{_word(rng, 5)}",
        json_offset=rng.choice(["offset", "start", "skip", "from"]),
        json_keys={k: rng.choice(pool) for k, pool in JSON_KEY_POOL.items()})


# --- data -------------------------------------------------------------------
def blind_tenders(spec: BlindSpec) -> list[LabTender]:
    out = []
    for blind_id, t in zip(spec.ids, make_tenders(f"blind-{spec.seed}")):
        docs = [type(d)(d.file_name.replace(t.id, blind_id), d.content) for d in t.docs]
        out.append(LabTender(**{**t.__dict__, "id": blind_id, "docs": docs}))
    return out


def blind_truth(spec: BlindSpec, base: str) -> list[Tender]:
    out = []
    for t in blind_tenders(spec):
        kw = {}
        if t.description:
            kw["description"] = t.description
        if t.cpv:
            kw["cpvCodes"] = t.cpv
        status = TenderStatus.closed if t.deadline < datetime.now(BERLIN) else TenderStatus.open
        out.append(Tender(
            id=t.id, url=f"{base}/blind/{spec.seed}/{spec.detail_seg}/{t.id}", title=t.title, buyerName=t.buyer,
            publicationDate=t.published, submissionDeadline=t.deadline, status=status,
            documents=[Document(fileName=d.file_name, sizeBytes=len(d.content), sha256=d.sha256, path="")
                       for d in t.docs],
            lastChangedAt=datetime(2026, 11, 20, tzinfo=BERLIN), referenceNumber=t.reference,
            procedureType=t.procedure, contractNature=t.nature,
            placeOfPerformance=Place(postalCode=t.place[0], city=t.place[1], countryCode="DE"), **kw))
    return out


# --- formatting -------------------------------------------------------------
def fmt_day(spec: BlindSpec, d: date) -> str:
    if spec.date_fmt == "iso":
        return d.strftime("%Y-%m-%d")
    if spec.date_fmt == "slash":
        return d.strftime("%d/%m/%Y")
    if spec.date_fmt == "long":
        return f"{d.day} {LABELS[spec.lang]['months'][d.month - 1]} {d.year}"
    return d.strftime("%d.%m.%Y")


def fmt_deadline(spec: BlindSpec, d: datetime) -> str:
    return f"{fmt_day(spec, d)}, {d:%H:%M}" if spec.date_fmt == "long" else f"{fmt_day(spec, d)} {d:%H:%M}"


def values(spec: BlindSpec, t: LabTender) -> dict[str, str]:
    lab = LABELS[spec.lang]
    cpv = ", ".join(f"{c}-{int(c) % 9}" if spec.cpv_check_digit else c for c in t.cpv)
    return {"id": t.id, "ref": t.reference, "title": t.title, "buyer": t.buyer, "published": fmt_day(spec, t.published),
            "deadline": fmt_deadline(spec, t.deadline), "procedure": PROC[spec.lang][t.procedure],
            "nature": NATURE[spec.lang][t.nature], "place": f"{t.place[0]} {t.place[1]}, {lab['country']}",
            "cpv": cpv, "description": t.description or ""}


def shell(spec: BlindSpec, title: str, body: str, script: str = "") -> str:
    brand = f"{_word(random.Random(spec.seed), 5).capitalize()} e-Procurement"
    return (f"<!doctype html><html lang='{spec.lang}'><head><meta charset='utf-8'><title>{html.escape(title)}"
            f"</title></head><body><header><b>{brand}</b> · <a href='#'>Login</a> · <a href='#'>FAQ</a></header>"
            f"<main>{body}</main><footer>© 2026 {brand}</footer>{script}</body></html>")


# --- pages ------------------------------------------------------------------
def render_list(spec: BlindSpec, root: str, page_index: int) -> str:
    lab, c = LABELS[spec.lang], spec.css
    items = blind_tenders(spec)
    start = page_index * spec.page_size
    chunk = items[start: start + spec.page_size]
    rows = []
    for t in chunk:
        v = values(spec, t)
        link = f"{root}/{spec.detail_seg}/{t.id}"
        cell = {k: (f"<a class='{c['link']}' href='{link}'>{html.escape(v[k])}</a>" if k == "title"
                    else html.escape(v[k])) for k in spec.order}
        if spec.layout == "table":
            rows.append(f"<tr class='{c['row']}'>" + "".join(f"<td class='{c[k]}'>{cell[k]}</td>" for k in spec.order)
                        + "</tr>")
        elif spec.layout == "cards":
            meta = "".join(f"<span class='{c[k]}'>{lab[k] if k != 'id' else lab['id']}: {cell[k]}</span> "
                           for k in spec.order if k != "title")
            rows.append(f"<article class='{c['row']}'><h3>{cell['title']}</h3><p class='{c['meta']}'>{meta}</p></article>")
        else:
            dd = "".join(f"<dt>{lab[k] if k in lab else k}</dt><dd class='{c[k]}'>{cell[k]}</dd>" for k in spec.order)
            rows.append(f"<dl class='{c['row']}'>{dd}</dl>")
    if spec.layout == "table":
        head = "".join(f"<th>{lab[k]}</th>" for k in spec.order)
        listing = f"<table class='{c['list']}'><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    else:
        listing = f"<div class='{c['list']}'>{''.join(rows)}</div>"
    pages = (len(items) + spec.page_size - 1) // spec.page_size
    has_next = page_index + 1 < pages
    if spec.paging == "page":
        nav = " ".join(f"<a href='{root}/?{spec.page_param}={p + 1}'>{p + 1}</a>" for p in range(pages))
        if has_next:
            nav += f" <a rel='next' href='{root}/?{spec.page_param}={page_index + 2}'>{lab['next']} ›</a>"
    elif spec.paging == "offset":
        nav = (f"<a rel='next' href='{root}/?{spec.page_param}={start + spec.page_size}'>{lab['next']} ›</a>"
               if has_next else "")
    else:
        nav = (f"<a rel='next' href='{root}/?{spec.page_param}={_cursor(spec, page_index + 1)}'>{lab['next']} ›</a>"
               if has_next else "")
    return shell(spec, lab["heading"], f"<h1>{lab['heading']}</h1><p>{len(items)}</p>{listing}"
                                       f"<nav class='{c['pager']}'>{nav}</nav>")


def _cursor(spec: BlindSpec, page_index: int) -> str:
    return f"{random.Random(f'{spec.seed}-{page_index}').getrandbits(40):x}{page_index:02d}"


def _page_from_cursor(spec: BlindSpec, cursor: str) -> int:
    try:
        n = int(cursor[-2:])
    except ValueError:
        raise HTTPException(400)
    if _cursor(spec, n) != cursor:
        raise HTTPException(400)
    return n


def render_detail(spec: BlindSpec, root: str, t: LabTender) -> str:
    lab, c = LABELS[spec.lang], spec.css
    v = values(spec, t)
    rows = "".join(f"<div class='{c['field']}'><span>{lab[k]}</span> <b class='{c['value']}'>{html.escape(v[k])}</b></div>"
                   for k in FIELDS if v[k])
    docs = "".join(f"<li><a href='{root}/{spec.file_seg}/{t.id}/{n}'>{html.escape(d.file_name)}</a></li>"
                   for n, d in enumerate(t.docs, 1))
    return shell(spec, t.title, f"<h1>{html.escape(t.title)}</h1><p>{lab['id']}: {t.id}</p>{rows}"
                                f"<h2>{lab['docs']}</h2><ul class='{c['docs']}'>{docs}</ul>")


def api_item(spec: BlindSpec, root: str, t: LabTender) -> dict:
    k = spec.json_keys
    v = values(spec, t)
    return {k["id"]: t.id, k["ref"]: t.reference, k["title"]: t.title, k["buyer"]: t.buyer,
            k["published"]: v["published"], k["deadline"]: v["deadline"], k["procedure"]: v["procedure"],
            k["nature"]: v["nature"], k["place"]: v["place"], k["cpv"]: v["cpv"].split(", ") if t.cpv else [],
            k["description"]: t.description,
            k["files"]: [{k["fname"]: d.file_name, k["fhref"]: f"{root}/{spec.file_seg}/{t.id}/{n}"}
                         for n, d in enumerate(t.docs, 1)]}


def json_shell(spec: BlindSpec, root: str, detail_id: str | None = None) -> str:
    k, lab = spec.json_keys, LABELS[spec.lang]
    api = f"{root}/{spec.api_path}"
    if detail_id:
        script = (f"<script>fetch('{api}/{detail_id}').then(r=>r.json()).then(t=>{{"
                  f"document.getElementById('app').innerHTML='<h1>'+t['{k['title']}']+'</h1><pre>'+"
                  "JSON.stringify(t,null,1)+'</pre>';});</script>")
    else:
        script = (f"<script>let o=0;async function load(){{const r=await fetch('{api}?{spec.json_offset}='+o);"
                  f"const j=await r.json();const el=document.getElementById('app');"
                  f"el.innerHTML+=j['{k['items']}'].map(t=>`<p><a href='{root}/{spec.detail_seg}/${{t['{k['id']}']}}'>"
                  f"${{t['{k['title']}']}}</a> – ${{t['{k['buyer']}']}} – ${{t['{k['deadline']}']}}</p>`).join('');"
                  f"const b=document.getElementById('more');if(o+{spec.page_size}<j['{k['total']}'])"
                  f"{{b.style.display='inline';}}else{{b.style.display='none';}}}}"
                  f"document.getElementById('more').onclick=()=>{{o+={spec.page_size};load();}};load();</script>")
    body = f"<h1>{lab['heading']}</h1><div id='app'></div><button id='more' style='display:none'>{lab['next']}</button>"
    return shell(spec, lab["heading"], body if not detail_id else "<div id='app'>…</div>", script)


def create_blind_app() -> FastAPI:
    app = FastAPI(title="Blind lab")

    def spec_for(seed: str) -> BlindSpec:
        if not seed.isdigit() or len(seed) > 12:
            raise HTTPException(404)
        return blind_spec(int(seed))

    def root(req: Request, seed: str) -> str:
        return f"{req.scope.get('root_path', '').rstrip('/')}/{seed}"

    def find(spec: BlindSpec, tid: str) -> LabTender:
        for t in blind_tenders(spec):
            if t.id == tid:
                return t
        raise HTTPException(404)

    @app.get("/{seed}/", response_class=HTMLResponse)
    async def listing(seed: str, req: Request):
        spec = spec_for(seed)
        if spec.source == "json":
            return json_shell(spec, root(req, seed))
        raw = req.query_params.get(spec.page_param)
        if spec.paging == "page":
            index = max(int(raw) - 1, 0) if raw and raw.isdigit() else 0
        elif spec.paging == "offset":
            index = (int(raw) // spec.page_size) if raw and raw.isdigit() else 0
        else:
            index = _page_from_cursor(spec, raw) if raw else 0
        return render_list(spec, root(req, seed), index)

    @app.get("/{seed}/truth")
    async def truth(seed: str, req: Request):
        spec = spec_for(seed)
        base = str(req.base_url).rstrip("/")
        mount = req.scope.get("root_path", "").rstrip("/")
        base = base[: -len(mount)] if mount and base.endswith(mount) else base
        base = base[: -len("/blind")] if base.endswith("/blind") else base
        return JSONResponse([tender_json(t) for t in blind_truth(spec, base)])

    @app.get("/{seed}/{path:path}")
    async def anything(seed: str, path: str, req: Request):
        """Detail pages, documents and the JSON API share one dispatcher: their paths differ per seed."""
        spec = spec_for(seed)
        parts = path.strip("/").split("/")
        r = root(req, seed)
        if len(parts) == 2 and parts[0] == spec.detail_seg:
            t = find(spec, parts[1])
            return HTMLResponse(json_shell(spec, r, t.id) if spec.source == "json" else render_detail(spec, r, t))
        if len(parts) == 3 and parts[0] == spec.file_seg and parts[2].isdigit():
            t, n = find(spec, parts[1]), int(parts[2])
            if not 1 <= n <= len(t.docs):
                raise HTTPException(404)
            d = t.docs[n - 1]
            return Response(d.content, media_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{d.file_name}"'})
        if spec.source == "json" and "/".join(parts[:3]) == spec.api_path:
            if len(parts) == 4:
                return api_item(spec, r, find(spec, parts[3]))
            if len(parts) == 3:
                items = blind_tenders(spec)
                raw = req.query_params.get(spec.json_offset, "0")
                off = int(raw) if raw.isdigit() else 0
                k = spec.json_keys
                return {k["total"]: len(items),
                        k["items"]: [api_item(spec, r, t) for t in items[off: off + spec.page_size]]}
        raise HTTPException(404)

    return app
