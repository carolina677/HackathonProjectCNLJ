"""Dashboard: morning view, live card fragments and the SSE event stream."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import cost, events

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


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


async def card_context(repo, p) -> dict:
    runs = await repo.run_details(p.id, limit=50)
    oldest = runs[-1]["id"] if runs else None
    story = [{"text": run_words(r, r["id"] == oldest), "when": ago(r["finished_at"] or r["started_at"]),
              "result": r["result"] or "running"} for r in runs[:3]]
    u = urlsplit(p.url)
    label = (u.netloc + u.path.rstrip("/"))[:60]
    return {"p": p.out(), "host": label, "story": story, "cost": cost.summary(p.id),
            "platform": (p.recipe or {}).get("platform"), "country": p.country}


def register(app: FastAPI) -> None:
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        repo = request.app.state.repo
        portals = await repo.list_portals()
        cards = [await card_context(repo, p) for p in portals]
        counts = {s: sum(c["p"].state == s for c in cards) for s in ("serving", "healing", "integrating", "failed")}
        return templates.TemplateResponse(request, "index.html", {
            "cards": cards, "counts": counts, "tenders": sum(c["p"].tenderCount for c in cards),
            "avg_usd": cost.per_portal_average(), "projected": cost.projected(4500),
            "lab": bool(request.app.state.settings.lab)})

    @app.get("/ui/portals/{pid}/card", response_class=HTMLResponse)
    async def card(pid: str, request: Request):
        repo = request.app.state.repo
        p = await repo.get_portal(pid)
        if not p:
            raise HTTPException(404, "portal not found")
        return templates.TemplateResponse(request, "_card.html", {"c": await card_context(repo, p)})

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
                await sub.aclose()

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
