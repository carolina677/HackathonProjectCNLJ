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


async def run_timelapse(app, base_url: str, *, speed: float = 1.0) -> dict:
    """Seed local Chaos Lab portals offline and play a fast-forward Notice -> Heal -> Serving cycle on ssr."""
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
    for p in await repo.list_portals():
        if "/lab/" in p.url:
            if sched is not None:
                await sched.cancel(p.id)
            await repo.delete_portal(p.id)
            cost.forget(p.id)
            rep_file = data_dir / "snapshots" / p.id / "repairs.json"
            rep_file.unlink(missing_ok=True)

    seeded = []
    ssr_id = ""
    ssr_old_dict = _ssr_recipe(base_url, "ssr", renamed=False)
    before_ref = ""
    fill_ok = {"title": 1.0, "buyerName": 1.0, "publicationDate": 1.0, "submissionDeadline": 1.0, "documents": 1.0}

    for slug in ("ssr", "multistep", "captcha-open-docs", "login"):
        url = f"{base_url}/lab/{slug}/"
        pid = await repo.create_portal(url)
        if sched is not None and hasattr(sched, "_last_enqueued"):
            sched._last_enqueued[pid] = time.monotonic()
        if slug == "login":
            rid = await repo.start_run(pid, RunKind.read)
            await repo.finish_run(rid, RunResult.failed, "Portal requires a login")
            await repo.set_state(pid, PortalState.failed, "Portal requires a login")
            cost.record(pid, "jev", 1800, 8, 0.00008)
            events.emit(pid, "state", {"from": "integrating", "to": "failed", "reason": "Portal requires a login"})
            seeded.append({"id": pid, "slug": slug, "state": "failed"})
            continue

        rec = ssr_old_dict if slug == "ssr" else _ssr_recipe(base_url, slug)
        snap_html = render_list(slug, f"{base_url}/lab", make_tenders(slug)[:10], 1, 23, None)
        ref = _write_snapshot(data_dir, pid, f"demo_{slug}_before.html", snap_html)
        if slug == "ssr":
            ssr_id = pid
            before_ref = ref
        await repo.set_recipe(pid, rec, snapshot_ref=ref, country="DE")
        count = await _seed_tenders(repo, data_dir, pid, slug, base_url)
        await repo.mark_success(pid, count, now_utc(), fill=fill_ok)
        rid = await repo.start_run(pid, RunKind.read)
        await repo.finish_run(rid, RunResult.ok)
        await repo.set_state(pid, PortalState.serving, None)
        cost.record(pid, "jev", 6400, 24, 0.00027)
        cost.record(pid, "llm", 11800, 620, 0.0298)
        events.emit(pid, "log", {"msg": f"Demo seeded {slug}: {count} tenders with PDFs"})
        events.emit(pid, "state", {"from": "integrating", "to": "serving", "reason": None})
        seeded.append({"id": pid, "slug": slug, "state": "serving"})

    async def _sequence() -> None:
        reason = "The tender list no longer matches the learned layout (the page changed)."
        try:
            await asyncio.sleep(1.5 / speed)
            STATE.mutate("ssr", "rename_classes")
            after_html = render_list("ssr", f"{base_url}/lab", make_tenders("ssr")[:10], 1, 23, None)
            after_ref = _write_snapshot(data_dir, ssr_id, f"{int(time.time() * 1000)}_after.html", after_html)

            rid_fail = await repo.start_run(ssr_id, RunKind.read)
            await repo.finish_run(rid_fail, RunResult.failed, reason)
            await repo.set_state(ssr_id, PortalState.healing, reason)
            events.emit(ssr_id, "log", {"msg": f"Read unhealthy: {reason}"})
            events.emit(ssr_id, "state", {"from": "serving", "to": "healing", "reason": reason})

            await asyncio.sleep(2.5 / speed)
            rid_rep = await repo.start_run(ssr_id, RunKind.repair)
            events.emit(ssr_id, "run", {"id": rid_rep, "kind": "repair", "status": "started"})
            events.emit(ssr_id, "explore", {"path": [f"{base_url}/lab/ssr/"], "blocked": None,
                                              "docs_reachable": True, "json_apis": [], "error": None})
            events.emit(ssr_id, "judgment", {
                "question": "What kind of page is this, for someone collecting public tenders?",
                "answer": "tender_list",
                "probabilities": {"tender_list": 0.96, "other": 0.03, "search_form": 0.01},
                "source": "jev", "ms": 42, "usd": 0.00004,
            })
            ssr_new_dict = _ssr_recipe(base_url, "ssr", renamed=True)
            diff = recipe_diff(Recipe.model_validate(ssr_old_dict), Recipe.model_validate(ssr_new_dict))
            cost.record(ssr_id, "llm", 5011, 1529, 0.0253)
            cost.record(ssr_id, "jev", 4200, 12, 0.00018)
            events.emit(ssr_id, "llm", {"purpose": "repair", "ms": 1450, "usd": 0.0253, "tokens": 6540})
            events.emit(ssr_id, "recipe", {"recipe": ssr_new_dict, "reason": "repaired", "diff": diff})
            events.emit(ssr_id, "verify", {
                "ok": True,
                "field_scores": {"title": 0.99, "buyerName": 0.98, "publicationDate": 0.97,
                                 "submissionDeadline": 0.99, "referenceNumber": 0.96, "cpvCodes": 0.95},
                "mask": [], "problems": [], "samples": ["LAB-0001", "LAB-0002", "LAB-0003"], "judged": True,
            })

            await asyncio.sleep(3.0 / speed)
            row = repairs.record(
                data_dir, ssr_id, ok=True, reason=reason, diff=diff, attempts=1,
                before_ref=before_ref, before_url=ssr_old_dict["list_url"], before_recipe=ssr_old_dict,
                after_ref=after_ref, after_url=ssr_new_dict["list_url"], after_recipe=ssr_new_dict,
            )
            await repo.set_recipe(ssr_id, ssr_new_dict, snapshot_ref=after_ref, country="DE")
            await repo.mark_success(ssr_id, 23, now_utc(), fill=fill_ok)
            await repo.set_state(ssr_id, PortalState.serving, None)
            await repo.finish_run(rid_rep, RunResult.ok)
            if row:
                events.emit(ssr_id, "compare", {"n": row["n"], "ok": True})
            events.emit(ssr_id, "run", {"id": rid_rep, "status": "finished", "result": "ok", "detail": None})
            events.emit(ssr_id, "state", {"from": "healing", "to": "serving", "reason": None})
            events.emit(ssr_id, "log", {"msg": "Repaired itself — open Theater or Briefly UI to inspect Before vs After"})
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_sequence())
    demo_tasks.add(task)
    task.add_done_callback(demo_tasks.discard)
    app.state.demo_tasks = demo_tasks
    return {"status": "started", "ssrPortalId": ssr_id, "portals": seeded}
