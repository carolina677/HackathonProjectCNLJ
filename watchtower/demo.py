"""Offline self-healing timelapse demo for the Chaos Lab (WATCHTOWER_LAB=1 only)."""

import asyncio
import json
import time
from pathlib import Path

from . import cost, events, repairs
from .brain.heal import recipe_diff
from .db import now_utc
from .lab.app import STATE, render_list
from .lab.generator import make_tenders, truth
from .models import Document, PortalState, RunKind, RunResult
from .recipe.dsl import Recipe

DETAIL_FIELDS = {
    "referenceNumber": {"label": "Vergabenummer"},
    "submissionDeadline": {"label": "Angebotsfrist", "normalizer": "datetime"},
    "procedureType": {"label": "Verfahrensart", "normalizer": "procedure"},
    "contractNature": {"label": "Art des Auftrags", "normalizer": "nature"},
    "cpvCodes": {"label": "CPV-Codes", "normalizer": "cpv_list"},
    "description": {"label": "Beschreibung"},
    "placeOfPerformance.postalCode": {"label": "Erfüllungsort", "regex": r"(\d{5})"},
    "placeOfPerformance.city": {"label": "Erfüllungsort", "regex": r"\d{5}\s+([^,]+)"},
    "placeOfPerformance.countryCode": {"label": "Erfüllungsort", "normalizer": "country"},
}


def _ssr_recipe(base_url: str, slug: str, *, renamed: bool = False) -> dict:
    row = "tr.vx-wor-rednet" if renamed else "tr.tender-row"
    id_sel = "td.vx-rn" if renamed else "td.nr"
    buyer_sel = "td.vx-reyub" if renamed else "td.buyer"
    pub_sel = "td.vx-dehsilbup" if renamed else "td.published"
    if slug == "multistep":
        docs = [{"selector": "a.tab", "attr": "href", "follow": True}, {"selector": "a.download", "attr": "href"}]
    elif renamed:
        docs = [{"selector": "ul.vx-stnemucod a", "attr": "href"}]
    else:
        docs = [{"selector": "ul.documents a", "attr": "href"}]
    return {
        "source": "html",
        "list_url": f"{base_url}/lab/{slug}/",
        "country": "DE",
        "pagination": {"kind": "page_param", "param": "page", "start": 1},
        "item": row,
        "fields": {
            "id": {"selector": id_sel},
            "title": {"selector": "td a"},
            "url": {"selector": "td a", "attr": "href", "normalizer": "url"},
            "buyerName": {"selector": buyer_sel},
            "publicationDate": {"selector": pub_sel, "normalizer": "date"},
        },
        "detail": {"source": "html", "url_field": "url", "fields": DETAIL_FIELDS},
        "documents": docs,
    }


async def _seed_tenders(repo, data_dir: Path, pid: str, slug: str, base_url: str) -> int:
    files_root = data_dir / "files"
    docs_by_tid: dict[str, list[Document]] = {}
    for lt in make_tenders(slug):
        items: list[Document] = []
        for d in lt.docs:
            dest = files_root / d.sha256[:2] / d.sha256
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(d.content)
            fid = await repo.save_document(
                portal_id=pid, tender_id=lt.id, file_name=d.file_name,
                size_bytes=len(d.content), sha256=d.sha256, stored_path=str(dest),
            )
            items.append(Document(fileName=d.file_name, sizeBytes=len(d.content), sha256=d.sha256, path=f"/files/{fid}"))
        docs_by_tid[lt.id] = items
    now = now_utc()
    tenders = [t.model_copy(update={"documents": docs_by_tid.get(t.id, []), "lastChangedAt": now})
               for t in truth(slug, base_url)]
    await repo.upsert_tenders(pid, tenders, now)
    return len(tenders)


def _write_snapshot(data_dir: Path, pid: str, name: str, html: str) -> str:
    d = data_dir / "snapshots" / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(html)
    return f"snapshots/{pid}/{name}"


def _spa_recipe(base_url: str, *, renamed: bool = False) -> dict:
    k = {"title": "bezeichnung", "authority": "vergabestelle", "deadline": "frist", "publishedAt": "veroeffentlicht",
         "files": "dokumente"} if renamed else {}
    key = lambda name: k.get(name, name)  # noqa: E731
    return {
        "source": "json_api", "list_url": f"{base_url}/lab/spa/api/tenders", "country": "DE",
        "pagination": {"kind": "offset", "param": "offset", "start": 0, "step": 10},
        "item": "$.items[*]",
        "fields": {
            "id": {"selector": "$.id"}, "title": {"selector": f"$.{key('title')}"},
            "url": {"selector": "$.id", "template": f"{base_url}/lab/spa/tender/{{value}}", "normalizer": "url"},
            "buyerName": {"selector": f"$.{key('authority')}"},
            "publicationDate": {"selector": f"$.{key('publishedAt')}", "normalizer": "date"},
            "submissionDeadline": {"selector": f"$.{key('deadline')}", "normalizer": "datetime"},
            "status": {"selector": "$.cancelled", "normalizer": "status"},
            "cpvCodes": {"selector": "$.cpv", "normalizer": "cpv_list"},
        },
        "documents": [{"selector": f"$.{key('files')}[*].href", "attr": "value"}],
    }


def _spa_snapshot(base_url: str) -> str:
    from .lab.app import api_item

    items = [api_item("spa", f"{base_url}/lab", t) for t in make_tenders("spa")[:3]]
    body = json.dumps({"total": 23, "items": items}, indent=2, ensure_ascii=False)
    return f"<!doctype html><html><body><pre>{body.replace('<', '&lt;')}</pre></body></html>"


# What breaks during the night, and what the read honestly reports for it (texts as the health check writes them).
BREAKS = [
    ("ssr", "rename_classes", "The tender list no longer matches the learned layout (the page changed)."),
    ("spa", "json_keys_rename", "Field title is suddenly empty in most tenders (0% filled, 100% before)."),
    ("multistep", "rename_classes", "The tender list no longer matches the learned layout (the page changed)."),
]
DOCS_LOCKED = "Document downloads lead to a login page instead of the files."


def _recipes(base_url: str, slug: str) -> tuple[dict, dict]:
    if slug == "spa":
        return _spa_recipe(base_url), _spa_recipe(base_url, renamed=True)
    return _ssr_recipe(base_url, slug), _ssr_recipe(base_url, slug, renamed=True)


def _snapshot_html(base_url: str, slug: str) -> str:
    if slug == "spa":
        return _spa_snapshot(base_url)
    return render_list(slug, f"{base_url}/lab", make_tenders(slug)[:10], 1, 23, None)


async def _clear_lab_portals(app, data_dir: Path) -> None:
    repo, sched = app.state.repo, getattr(app.state, "scheduler", None)
    for p in await repo.list_portals():
        if "/lab/" in p.url:
            if sched is not None:
                await sched.cancel(p.id)
            await repo.delete_portal(p.id)
            cost.forget(p.id)
            (data_dir / "snapshots" / p.id / "repairs.json").unlink(missing_ok=True)


NEW_PORTAL = "spa"  # attached on screen at the start of the replay


async def run_timelapse(app, base_url: str, *, speed: float = 1.0) -> dict:
    """Replay, offline and in about 40 seconds, what a night looks like: five portals deliver, three change their
    layout at once, the pulse notices, every one repairs itself while its tenders stay served, and one portal that
    locks its documents behind a login is reported honestly. Lab only; every step is labelled as a replay."""
    base_url = base_url.rstrip("/")
    repo = app.state.repo
    data_dir = Path(app.state.settings.data_dir)
    sched = getattr(app.state, "scheduler", None)
    demo_tasks: set[asyncio.Task] = getattr(app.state, "demo_tasks", set())
    for t in list(demo_tasks):
        t.cancel()
    if demo_tasks:
        await asyncio.gather(*list(demo_tasks), return_exceptions=True)

    STATE.reset()
    await _clear_lab_portals(app, data_dir)
    fill_ok = {"title": 1.0, "buyerName": 1.0, "publicationDate": 1.0, "submissionDeadline": 1.0, "documents": 1.0}
    seeded: list[dict] = []
    ids: dict[str, str] = {}
    counts: dict[str, int] = {}
    before: dict[str, str] = {}
    usd = 0.0
    tally = {"noticed": 0, "repairs": 0, "needs_human": 0, "kept_served": 0, "tenders_lost": 0}

    def story(step: str, text: str, pid: str | None = None, **extra) -> None:
        events.emit(pid, "story", {"mode": "replay", "step": step, "text": text, **tally,
                                   "usd": round(usd, 4), **extra})

    def spend(pid: str, provider: str, tin: int, tout: int, amount: float) -> None:
        nonlocal usd
        cost.record(pid, provider, tin, tout, amount)
        usd += amount

    for slug in ("ssr", "spa", "multistep", "captcha-open-docs", "login"):
        pid = await repo.create_portal(f"{base_url}/lab/{slug}/")
        ids[slug] = pid
        if sched is not None and hasattr(sched, "_last_enqueued"):
            sched._last_enqueued[pid] = time.monotonic()
            sched._cancelled.discard(pid)  # same id as the portal deleted above: watch it again (pulse, reads)
        if slug == NEW_PORTAL:
            events.emit(pid, "state", {"from": "integrating", "to": "integrating", "reason": None})
            seeded.append({"id": pid, "slug": slug, "state": "integrating"})
            continue
        if slug == "login":
            rid = await repo.start_run(pid, RunKind.read)
            await repo.finish_run(rid, RunResult.failed, "Portal requires a login")
            await repo.set_state(pid, PortalState.failed, "Portal requires a login")
            spend(pid, "jev", 1800, 8, 0.00008)
            events.emit(pid, "state", {"from": "integrating", "to": "failed", "reason": "Portal requires a login"})
            seeded.append({"id": pid, "slug": slug, "state": "failed"})
            tally["needs_human"] += 1
            continue
        rec = _recipes(base_url, slug)[0]
        before[slug] = _write_snapshot(data_dir, pid, f"demo_{slug}_before.html", _snapshot_html(base_url, slug))
        await repo.set_recipe(pid, rec, snapshot_ref=before[slug], country="DE")
        counts[slug] = await _seed_tenders(repo, data_dir, pid, slug, base_url)
        await repo.mark_success(pid, counts[slug], now_utc(), fill=fill_ok)
        rid = await repo.start_run(pid, RunKind.read)
        await repo.finish_run(rid, RunResult.ok)
        await repo.set_state(pid, PortalState.serving, None)
        spend(pid, "jev", 6400, 24, 0.00027)
        spend(pid, "llm", 11800, 620, 0.0298)
        events.emit(pid, "log", {"msg": f"Demo seeded {slug}: {counts[slug]} tenders with PDFs"})
        events.emit(pid, "state", {"from": "integrating", "to": "serving", "reason": None})
        seeded.append({"id": pid, "slug": slug, "state": "serving"})

    total = sum(counts.values())
    story("start", f"Replay of one night: 4 portals deliver {total} tenders with their documents. "
                   "The portal behind a login reports itself as blocked. A new portal is about to be added.",
          tenders=total)

    async def _attach(slug: str) -> None:
        """The attach flow, replayed: explore, one LLM call writes the recipe, Jev verifies, then plain code."""
        nonlocal total
        pid = ids[slug]
        url = f"{base_url}/lab/{slug}/"
        story("attach", f"New portal added: only a URL, {url}. Watchtower has never seen it.", pid, slug=slug)
        events.emit(pid, "page", {"url": url, "status": 200, "via": "browser", "ms": 1830,
                                  "json_apis": [f"{base_url}/lab/{slug}/api/tenders"]})
        events.emit(pid, "judgment", {
            "question": "What kind of page is this, for someone collecting public tenders?", "answer": "tender_list",
            "probabilities": {"tender_list": 0.91, "search_form": 0.06, "other": 0.03},
            "source": "jev", "ms": 38, "usd": 0.00004})
        spend(pid, "jev", 3100, 9, 0.00013)
        await asyncio.sleep(2.0 / speed)
        story("explore", "Jev: this is a tender list, built in JavaScript. Watchtower found the JSON API behind it.",
              pid, slug=slug)
        await asyncio.sleep(2.0 / speed)
        rec = _recipes(base_url, slug)[0]
        spend(pid, "llm", 11800, 620, 0.0248)
        events.emit(pid, "llm", {"purpose": "induce", "ms": 9400, "usd": 0.0248, "tokens": 12420})
        events.emit(pid, "recipe", {"recipe": rec, "reason": "learned"})
        story("recipe", "One LLM call wrote a recipe: where the list is, which field is which, where the PDFs are. "
                        "Cost: $0.025.", pid, slug=slug)
        await asyncio.sleep(2.0 / speed)
        events.emit(pid, "verify", {
            "ok": True, "field_scores": {"title": 0.99, "buyerName": 0.98, "publicationDate": 0.98,
                                         "submissionDeadline": 0.97},
            "mask": [], "problems": [], "samples": [f"LAB-000{i}" for i in range(1, 10)] + ["LAB-0010"],
            "judged": True})
        spend(pid, "jev", 9800, 30, 0.00041)
        story("verify", "Jev checked 10 tenders against their own pages: 97–99 % match on every field. "
                        "Nothing is served before this check passes.", pid, slug=slug)
        await asyncio.sleep(1.5 / speed)
        before[slug] = _write_snapshot(data_dir, pid, f"demo_{slug}_before.html", _snapshot_html(base_url, slug))
        await repo.set_recipe(pid, rec, snapshot_ref=before[slug], country="DE")
        counts[slug] = await _seed_tenders(repo, data_dir, pid, slug, base_url)
        await repo.mark_success(pid, counts[slug], now_utc(), fill=fill_ok)
        rid = await repo.start_run(pid, RunKind.read)
        await repo.finish_run(rid, RunResult.ok)
        await repo.set_state(pid, PortalState.serving, None)
        events.emit(pid, "state", {"from": "integrating", "to": "serving", "reason": None})
        total += counts[slug]
        story("learned", f"{slug} delivers {counts[slug]} tenders with their PDFs. From now on every read is plain "
                         "code: no LLM.", pid, slug=slug, tenders=total)

    async def _break(slug: str, mutation: str, reason: str) -> None:
        pid = ids[slug]
        STATE.mutate(slug, mutation)
        rid = await repo.start_run(pid, RunKind.read)
        await repo.finish_run(rid, RunResult.failed, reason)
        await repo.set_state(pid, PortalState.healing, reason)
        events.emit(pid, "log", {"msg": f"Pulse: {reason} Reading now."})
        events.emit(pid, "state", {"from": "serving", "to": "healing", "reason": reason})
        tally["noticed"] += 1
        tally["kept_served"] += counts[slug]
        story("notice", f"Pulse noticed {slug}: {reason} Its {counts[slug]} tenders stay served.", pid, slug=slug)

    async def _repair(slug: str, reason: str, secs: float) -> None:
        pid = ids[slug]
        t0 = time.monotonic()
        old, new = _recipes(base_url, slug)
        rid = await repo.start_run(pid, RunKind.repair)
        events.emit(pid, "run", {"id": rid, "kind": "repair", "status": "started"})
        story("repair", f"Repairing {slug}: the LLM reads the changed page and patches the recipe.", pid, slug=slug)
        events.emit(pid, "judgment", {
            "question": "What kind of page is this, for someone collecting public tenders?", "answer": "tender_list",
            "probabilities": {"tender_list": 0.96, "other": 0.03, "search_form": 0.01},
            "source": "jev", "ms": 42, "usd": 0.00004})
        diff = recipe_diff(Recipe.model_validate(old), Recipe.model_validate(new))
        llm_usd = 0.021 + 0.004 * len(diff) / 5
        spend(pid, "llm", 5011, 1529, llm_usd)
        spend(pid, "jev", 4200, 12, 0.00018)
        events.emit(pid, "llm", {"purpose": "repair", "ms": 1450, "usd": llm_usd, "tokens": 6540})
        events.emit(pid, "recipe", {"recipe": new, "reason": "repaired", "diff": diff})
        events.emit(pid, "verify", {
            "ok": True, "field_scores": {"title": 0.99, "buyerName": 0.98, "publicationDate": 0.97,
                                         "submissionDeadline": 0.99},
            "mask": [], "problems": [], "samples": ["LAB-0001", "LAB-0002", "LAB-0003"], "judged": True})
        await asyncio.sleep(secs / speed)
        after_ref = _write_snapshot(data_dir, pid, f"{int(time.time() * 1000)}_after.html",
                                    _snapshot_html(base_url, slug))
        row = repairs.record(data_dir, pid, ok=True, reason=reason, diff=diff, attempts=1,
                             before_ref=before[slug], before_url=old["list_url"], before_recipe=old,
                             after_ref=after_ref, after_url=new["list_url"], after_recipe=new)
        await repo.set_recipe(pid, new, snapshot_ref=after_ref, country="DE")
        await repo.mark_success(pid, counts[slug], now_utc(), fill=fill_ok)
        await repo.set_state(pid, PortalState.serving, None)
        await repo.finish_run(rid, RunResult.ok)
        if row:
            events.emit(pid, "compare", {"n": row["n"], "ok": True})
        events.emit(pid, "run", {"id": rid, "status": "finished", "result": "ok", "detail": None})
        events.emit(pid, "state", {"from": "healing", "to": "serving", "reason": None})
        tally["repairs"] += 1
        took = (time.monotonic() - t0) * speed
        story("repaired", f"{slug} repaired itself in {took:.0f} s for ${llm_usd:.3f}. "
                          f"{len(diff)} recipe lines changed; open Theater for before vs after.", pid, slug=slug,
              seconds=round(took, 1))

    async def _lock_documents() -> None:
        slug, pid = "captcha-open-docs", ids["captcha-open-docs"]
        STATE.mutate(slug, "docs_login")
        rid = await repo.start_run(pid, RunKind.read)
        await repo.finish_run(rid, RunResult.failed, DOCS_LOCKED)
        await repo.set_state(pid, PortalState.healing, DOCS_LOCKED)
        events.emit(pid, "state", {"from": "serving", "to": "healing", "reason": DOCS_LOCKED})
        tally["noticed"] += 1
        tally["kept_served"] += counts[slug]
        story("notice", f"{slug}: {DOCS_LOCKED} The login page is not stored as a document.", pid, slug=slug)
        await asyncio.sleep(2.0 / speed)
        why = "Portal requires a login for its documents"
        await repo.set_state(pid, PortalState.failed, why)
        events.emit(pid, "state", {"from": "healing", "to": "failed", "reason": why})
        tally["needs_human"] += 1
        story("honest", f"{slug} needs a person: no login is ever entered or guessed. Its tenders stay served.",
              pid, slug=slug)

    async def _sequence() -> None:
        try:
            await asyncio.sleep(1.5 / speed)
            await _attach(NEW_PORTAL)
            await asyncio.sleep(2.0 / speed)
            story("break", "02:14 at night: three portals change their layout at the same time. Nobody presses refresh.")
            for slug, mutation, reason in BREAKS:
                await asyncio.sleep(0.8 / speed)
                await _break(slug, mutation, reason)
            await asyncio.sleep(1.5 / speed)
            for slug, _, reason in BREAKS:
                await _repair(slug, reason, 3.0)
                await asyncio.sleep(0.6 / speed)
            await _lock_documents()
            await asyncio.sleep(1.5 / speed)
            story("done", f"Morning: {tally['repairs']} portals repaired themselves, {tally['kept_served']} tenders "
                          f"stayed served during the night, none lost. {tally['needs_human']} portals need a person, "
                          f"with the reason. Model cost tonight: ${usd:.2f}.", tenders=total)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_sequence())
    demo_tasks.add(task)
    task.add_done_callback(demo_tasks.discard)
    app.state.demo_tasks = demo_tasks
    return {"status": "started", "ssrPortalId": ids["ssr"], "portals": seeded}


def chaos_mutation(slug: str) -> str:
    """A repairable change the 15-minute pulse can see. pagination_style leaves page one as it was, so only the
    12-hour read would notice it: useless for a live demo."""
    import random

    from .lab.mutations import REPAIRABLE

    html_only = ("rename_classes", "reshuffle_layout", "date_format")
    options = [m for m in REPAIRABLE if m != "pagination_style" and not STATE.has(slug, m)
               and (m in ("json_keys_rename", "api_path_move")) == (slug == "spa")
               and (slug == "spa" or m in html_only)]
    name = random.choice(options) if options else ("api_path_move" if slug == "spa" else "date_format")
    STATE.mutate(slug, name)
    return name


LIVE_BREAK = ("ssr", "multistep", "spa")  # preference order: ssr is the first lab card on the Overview


def _lab_slug(url: str) -> str | None:
    return url.rstrip("/").rsplit("/lab/", 1)[-1] if "/lab/" in url else None


async def run_chaos(app, *, limit: int = 1) -> list[dict]:
    """Live, no replay: change the layout of `limit` serving lab portals and press nothing.
    The pulse notices, the real repair runs (real model calls)."""
    from datetime import datetime, timezone

    broken = []
    sched = getattr(app.state, "scheduler", None)
    portals = await app.state.repo.list_portals()
    portals.sort(key=lambda p: LIVE_BREAK.index(s) if (s := _lab_slug(p.url)) in LIVE_BREAK else len(LIVE_BREAK))
    for p in portals:
        slug = _lab_slug(p.url)
        if slug not in LIVE_BREAK or p.state != PortalState.serving:
            continue
        if hasattr(sched, "pulse_soon"):
            sched.pulse_soon(p.id)  # don't make the audience wait up to one pulse interval
        broken.append({"id": p.id, "slug": slug, "mutation": chaos_mutation(slug),
                       "at": datetime.now(timezone.utc).isoformat()})
        if len(broken) >= limit:
            break
    every = getattr(app.state.settings, "pulse_interval_seconds", 900)
    names = ", ".join(f"{b['slug']} ({b['mutation']})" for b in broken) or "none (no serving lab portal)"
    events.emit(None, "story", {"mode": "live", "step": "chaos",
                                "text": f"Live: changed {names}. Nobody presses refresh: the pulse (every {every} s, "
                                        f"brought forward for the demo) has to notice it, then the real repair runs."})
    return broken
