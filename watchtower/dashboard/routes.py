"""Dashboard: morning view, live card fragments and the SSE event stream."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import cost, events, repairs, tracing

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
# Changes whenever a static file changes, so browsers fetch the new CSS/JS instead of a cached copy.
templates.env.globals["static_v"] = format(max(f.stat().st_mtime_ns for f in (HERE / "static").iterdir()), "x")


def ago(dt) -> str:
    if not dt:
        return "never"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    s = int((datetime.now(timezone.utc) - dt).total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


templates.env.filters["ago"] = ago


def lab_slug(request: Request, url: str) -> str | None:
    """Break-it is offered only for Chaos Lab portals, and only when the lab is switched on."""
    if not request.app.state.settings.lab:
        return None
    from ..lab.app import SLUGS

    parts = [x for x in urlsplit(url).path.split("/") if x]
    return parts[1] if len(parts) >= 2 and parts[0] == "lab" and parts[1] in SLUGS else None


def judges(request: Request):
    st = request.app.state
    if getattr(st, "judges", None) is None:
        pl = getattr(st, "pipeline", None)
        if pl is not None and pl.jev is not None:
            st.judges = (pl.jev, pl.llm)
        else:
            from ..pipeline import make_judges

            st.judges = make_judges(st.settings)
    return st.judges


def run_words(r: dict, first: bool) -> str:
    kind, res, detail = r["kind"], r["result"], r.get("detail")
    if res is None:
        return "Learning the portal…" if first else ("Repairing…" if kind == "repair" else "Reading…")
    if kind == "repair":
        return "Repaired itself" if res == "ok" else "Repair failed — needs a human"
    if first:
        return "Learned the portal" if res == "ok" else "Could not learn the portal"
    if res == "ok":
        return "Read the portal, no model call"
    return "Read stopped: " + (detail or "the portal changed")


def _host_label(url: str) -> str:
    u = urlsplit(url)
    return (u.netloc + u.path.rstrip("/"))[:60] or url


async def card_context(repo, p, request: Request | None = None) -> dict:
    runs = await repo.run_details(p.id, limit=50)
    oldest = runs[-1]["id"] if runs else None
    story = [{"text": run_words(r, r["id"] == oldest), "when": ago(r["finished_at"] or r["started_at"]),
              "result": r["result"] or "running"} for r in runs[:3]]
    portal_repairs = repairs.list_repairs(request.app.state.settings.data_dir, p.id) if request is not None else []
    return {"p": p.out(), "host": _host_label(p.url), "story": story, "cost": cost.summary(p.id),
            "platform": (p.recipe or {}).get("platform"), "country": p.country,
            "lab": lab_slug(request, p.url) if request is not None else None,
            "latest_repair": portal_repairs[0] if portal_repairs else None,
            "repairs_count": len(portal_repairs)}


def register(app: FastAPI) -> None:
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        repo = request.app.state.repo
        portals = await repo.list_portals()
        cards = [await card_context(repo, p, request) for p in portals]
        counts = {s: sum(c["p"].state == s for c in cards) for s in ("serving", "healing", "integrating", "failed")}
        return templates.TemplateResponse(request, "index.html", {
            "cards": cards, "counts": counts, "tenders": sum(c["p"].tenderCount for c in cards),
            "avg_usd": cost.per_portal_average(), "projected": cost.projected(4500),
            "activity": await repo.activity_7d(),
            "lab": bool(request.app.state.settings.lab), "traces_ui": tracing.ui_url()})

    @app.get("/briefly")
    async def briefly():
        return RedirectResponse("/")

    @app.post("/demo/timelapse", status_code=202)
    async def demo_timelapse(request: Request):
        if not request.app.state.settings.lab:
            raise HTTPException(404, "Chaos Lab is disabled")
        from ..demo import run_timelapse

        return await run_timelapse(request.app, str(request.base_url).rstrip("/"))

    @app.get("/traces")
    async def traces():
        """Recent LLM and Jev calls recorded in MLflow (empty when tracing is off)."""
        import asyncio

        rows = await asyncio.to_thread(tracing.recent, 30)
        return {"enabled": tracing.ui_url() is not None, "ui": tracing.ui_url(), "traces": rows}

    @app.get("/ui/portals/{pid}/card", response_class=HTMLResponse)
    async def card(pid: str, request: Request):
        repo = request.app.state.repo
        p = await repo.get_portal(pid)
        if not p:
            raise HTTPException(404, "portal not found")
        return templates.TemplateResponse(request, "_card.html", {"c": await card_context(repo, p, request)})

    @app.get("/ui/summary")
    async def ui_summary(request: Request):
        repo = request.app.state.repo
        portals = await repo.list_portals()
        counts = {s: sum(p.state.value == s for p in portals) for s in ("serving", "healing", "integrating", "failed")}
        return {
            **counts,
            "tenders": sum(p.tenderCount for p in portals),
            "avg_usd": cost.per_portal_average(),
            "projected": cost.projected(4500),
            "lab": bool(request.app.state.settings.lab),
            "activity": await repo.activity_7d(),
        }

    @app.get("/ui/tenders")
    async def ui_tenders(request: Request):
        """Aggregated live tenders across all active portals for the unified dashboard."""
        repo = request.app.state.repo
        portals = await repo.list_portals()
        collected: list[tuple[int, float, dict]] = []
        for p in portals:
            host = _host_label(p.url)
            for t in await repo.list_tenders(p.id):
                data = t.model_dump(mode="json", exclude_none=True)
                data["portalId"] = p.id
                data["portalUrl"] = p.url
                data["portalHost"] = host
                if p.country:
                    data["portalCountry"] = p.country
                priority = 0 if data.get("status") == "open" else 1
                ts = t.lastChangedAt.timestamp() if t.lastChangedAt else 0.0
                collected.append((priority, -ts, data))
        collected.sort(key=lambda item: (item[0], item[1]))
        return {"total": len(collected), "tenders": [item[2] for item in collected[:500]]}

    async def _portal(request: Request, pid: str):
        p = await request.app.state.repo.get_portal(pid)
        if not p:
            raise HTTPException(404, "portal not found")
        return p

    @app.get("/portals/{pid}/theater", response_class=HTMLResponse)
    async def theater(pid: str, request: Request):
        p = await _portal(request, pid)
        c = await card_context(request.app.state.repo, p, request)
        return templates.TemplateResponse(request, "theater.html", {"c": c, "recipe": p.recipe,
                                                                    "projected": cost.projected(4500)})

    @app.get("/portals/{pid}/timeline")
    async def timeline(pid: str, request: Request):
        await _portal(request, pid)
        return events.history(pid, limit=400)

    @app.get("/portals/{pid}/repairs")
    async def repair_list(pid: str, request: Request):
        """Every repair of this portal, newest first, with what changed (for the before/after view)."""
        from .. import repairs

        await _portal(request, pid)
        return repairs.list_repairs(request.app.state.settings.data_dir, pid)

    @app.get("/portals/{pid}/repairs/{n}/{side}.html", response_class=HTMLResponse)
    async def repair_page(pid: str, n: int, side: str, request: Request):
        """The list page before or after a repair, highlighted and inert (scripts forbidden)."""
        from .. import repairs

        await _portal(request, pid)
        html = repairs.page(request.app.state.settings.data_dir, pid, n, side)
        if html is None:
            raise HTTPException(404, "no such repair snapshot")
        return HTMLResponse(html, headers={"Content-Security-Policy": repairs.CSP, "X-Content-Type-Options": "nosniff",
                                           "Cache-Control": "no-store"})

    @app.get("/portals/{pid}/cost")
    async def portal_cost(pid: str, request: Request):
        await _portal(request, pid)
        return {**cost.summary(pid), "avg_usd": cost.per_portal_average(), "projected_4500": cost.projected(4500)}

    @app.post("/portals/{pid}/break", status_code=202)
    async def break_it(pid: str, request: Request):
        p = await _portal(request, pid)
        slug = lab_slug(request, p.url)
        if not slug:
            raise HTTPException(400, "Break-it works on Chaos Lab portals only")
        from ..lab.app import break_portal
        from ..models import RunKind

        name = break_portal(slug)
        events.emit(pid, "log", {"msg": f"Chaos Lab: applied mutation '{name}' — watching for it"})
        rid = await request.app.state.scheduler.enqueue(pid, RunKind.read)
        return {"mutation": name, "runId": rid}

    @app.post("/portals/{pid}/race")
    async def race(pid: str, request: Request):
        """Same page-type question to Jev and to the frontier LLM, at the same time."""
        import time

        from pydantic import BaseModel

        from ..brain.explore import PAGE_TYPES, classify_page
        from ..fetch import Fetcher, Page
        from ..recipe.engine import visible_text
        from ..scheduler import HostLimiter

        p = await _portal(request, pid)
        st = request.app.state
        html = None
        if p.snapshot_ref:
            f = Path(st.settings.data_dir) / p.snapshot_ref
            html = f.read_text() if f.exists() else None
        if html is None:
            limiter = getattr(st, "limiter", None) or HostLimiter(st.settings.min_request_interval)
            pg = await Fetcher(pid, limiter).get(p.url)
            html, final = pg.html, pg.final_url
        else:
            final = p.url
        page = Page(url=p.url, final_url=final, status=200, html=html, headers={})
        jev, llm = judges(request)
        jev = jev.without_llm() if hasattr(jev, "without_llm") else jev

        class PageType(BaseModel):
            choice: str

        async def run_jev():
            before, t0 = cost.summary(pid)["jev_usd"], time.monotonic()
            c = await classify_page(page, jev, pid)
            return {"answer": c.choice, "probabilities": c.probabilities, "ms": int((time.monotonic() - t0) * 1000),
                    "usd": cost.summary(pid)["jev_usd"] - before}

        async def run_llm():
            before, t0 = cost.summary(pid)["llm_usd"], time.monotonic()
            options = "\n".join(f"- {k}: {v}" for k, v in PAGE_TYPES.items())
            out = await llm.complete_json(
                "You classify web pages for a public-tender crawler. Answer with one option key.",
                f"Options:\n{options}\n\nURL: {final}\nPage text:\n{visible_text(html, 6000)}",
                PageType, portal_id=pid, purpose="race", attempts=1)
            return {"answer": out.choice, "ms": int((time.monotonic() - t0) * 1000),
                    "usd": cost.summary(pid)["llm_usd"] - before}

        async def safe(coro):
            try:
                return await coro
            except Exception as e:
                return {"error": (str(e).splitlines() or ["error"])[0][:90], "answer": None, "ms": None, "usd": 0.0}

        a, b = await asyncio.gather(safe(run_jev()), safe(run_llm()))
        events.emit(pid, "race", {"jev": a, "llm": b})
        return {"jev": a, "llm": b}

    _brief_cache: dict = {}

    async def _briefing(request: Request) -> str:
        from datetime import timedelta

        from ..briefing import briefing_text

        repo = request.app.state.repo
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        return briefing_text(await repo.list_portals(), await repo.runs_since(since))

    @app.get("/briefing.txt", response_class=PlainTextResponse)
    async def briefing_txt(request: Request):
        return await _briefing(request)

    @app.get("/briefing.mp3")
    async def briefing_mp3(request: Request):
        """Spoken briefing, cached for 5 minutes per text. 404 when ElevenLabs is not configured."""
        import time

        from ..briefing import synthesize

        st = request.app.state.settings
        if not (st.elevenlabs_api_key and st.elevenlabs_voice_id):
            raise HTTPException(404, "ElevenLabs is not configured; see /briefing.txt")
        text = await _briefing(request)
        hit = _brief_cache.get("mp3")
        if not hit or hit[0] != text or time.monotonic() - hit[1] > 300:
            try:
                hit = (text, time.monotonic(), await synthesize(text, st))
            except Exception as e:
                raise HTTPException(502, f"ElevenLabs failed: {str(e)[:120]}")
            _brief_cache["mp3"] = hit
        return Response(hit[2], media_type="audio/mpeg", headers={"Cache-Control": "max-age=300"})

    @app.get("/events")
    async def stream(request: Request, limit: int | None = None, portal: str | None = None):
        async def gen():
            sub = events.subscribe()
            nxt = asyncio.ensure_future(sub.__anext__())
            sent = 0
            try:
                yield ": connected\n\n"
                while limit is None or sent < limit:
                    done, _ = await asyncio.wait({nxt}, timeout=15)
                    if not done:
                        if await request.is_disconnected():
                            break
                        yield ": keep-alive\n\n"
                        continue
                    ev = nxt.result()
                    nxt = asyncio.ensure_future(sub.__anext__())
                    if portal and ev["portal_id"] not in (portal, None):
                        continue
                    yield f"event: {ev['kind']}\ndata: {json.dumps(ev, default=str)}\n\n"
                    sent += 1
            finally:
                nxt.cancel()
                await asyncio.gather(nxt, return_exceptions=True)  # let the pending read stop before closing
                await sub.aclose()

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
