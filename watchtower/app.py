import asyncio
import mimetypes
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import cost, tracing
from .config import Settings, get_settings
from .db import Repo
from .models import RunKind, tender_json


class PortalIn(BaseModel):
    url: str


class _NullScheduler:
    """Stand-in until the real scheduler is attached (tests, or start_scheduler=False)."""

    def __init__(self, repo: Repo):
        self.repo = repo

    async def enqueue(self, pid: str, kind: RunKind = RunKind.read) -> str:
        return await self.repo.running_run(pid, kind) or await self.repo.start_run(pid, kind)

    async def cancel(self, pid: str) -> None:
        return None


def _404(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def create_app(settings: Settings | None = None, start_scheduler: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tracing.setup(settings)
        repo = Repo(settings.data_dir)
        await repo.init()
        if start_scheduler:
            await repo.fail_orphaned_runs()
        cost.reset()
        cost.load(await repo.load_costs())
        cost_tasks: set[asyncio.Task] = set()

        def _persist_cost(pid: str, row: dict[str, float]) -> None:
            loop = asyncio.get_running_loop()
            t = loop.create_task(repo.save_cost(pid, row))
            cost_tasks.add(t)
            t.add_done_callback(cost_tasks.discard)

        cost.set_persister(_persist_cost)
        app.state.repo = repo
        app.state.settings = settings
        app.state.scheduler = _NullScheduler(repo)
        if start_scheduler:
            from .pipeline import build_pipeline
            from .scheduler import HostLimiter, Scheduler

            limiter = HostLimiter(settings.min_request_interval)
            app.state.limiter = limiter
            ref: list = []
            pipeline = build_pipeline(repo, settings, limiter, is_cancelled=lambda pid: ref[0].is_cancelled(pid))
            sched = Scheduler(repo, pipeline, settings)
            ref.append(sched)
            app.state.pipeline = pipeline
            app.state.scheduler = sched
            sched.start()
            app.state.shutdown_hooks.append(lambda a: sched.stop())
        hooks = getattr(app.state, "startup_hooks", [])
        for h in hooks:
            await h(app, start_scheduler)
        try:
            yield
        finally:
            cost.clear_persister()
            for h in getattr(app.state, "shutdown_hooks", []):
                await h(app)
            if cost_tasks:
                await asyncio.gather(*list(cost_tasks), return_exceptions=True)
            await repo.close()

    app = FastAPI(title="Watchtower", lifespan=lifespan)
    app.state.startup_hooks = []
    app.state.shutdown_hooks = []

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid request", "details": exc.errors()}, status_code=422)

    def repo(request: Request) -> Repo:
        return request.app.state.repo

    @app.post("/portals", status_code=201)
    async def add_portal(body: PortalIn, request: Request):
        try:
            pid = await repo(request).create_portal(body.url)
        except ValueError:
            raise HTTPException(status_code=422, detail="url must be a web address such as https://portal.example/tenders")
        p = await repo(request).get_portal(pid)
        if p and p.state.value == "integrating" and not await repo(request).running_run(pid):
            await request.app.state.scheduler.enqueue(pid, RunKind.read)
        return {"id": pid}

    @app.get("/portals")
    async def list_portals(request: Request):
        return [p.out().model_dump(mode="json") for p in await repo(request).list_portals()]

    @app.get("/portals/{pid}")
    async def get_portal(pid: str, request: Request):
        p = await repo(request).get_portal(pid)
        if not p:
            raise _404("portal")
        return p.out().model_dump(mode="json")

    @app.delete("/portals/{pid}", status_code=204)
    async def delete_portal(pid: str, request: Request):
        if not await repo(request).get_portal(pid):
            raise _404("portal")
        await request.app.state.scheduler.cancel(pid)
        await repo(request).delete_portal(pid)
        cost.forget(pid)
        forget = getattr(getattr(request.app.state, "pipeline", None), "forget", None)
        if forget:
            forget(pid)
        return Response(status_code=204)

    @app.post("/portals/{pid}/refresh", status_code=202)
    async def refresh(pid: str, request: Request):
        if not await repo(request).get_portal(pid):
            raise _404("portal")
        rid = await request.app.state.scheduler.enqueue(pid, RunKind.read)
        return {"runId": rid}

    @app.get("/portals/{pid}/tenders")
    async def tenders(pid: str, request: Request, changedSince: datetime | None = None):
        if not await repo(request).get_portal(pid):
            raise _404("portal")
        return [tender_json(t) for t in await repo(request).list_tenders(pid, changedSince)]

    @app.get("/portals/{pid}/runs")
    async def runs(pid: str, request: Request):
        if not await repo(request).get_portal(pid):
            raise _404("portal")
        return [r.model_dump(mode="json") for r in await repo(request).list_runs(pid)]

    @app.get("/files/{fid}")
    async def file(fid: str, request: Request):
        d = await repo(request).get_document(fid)
        if not d or not Path(d.stored_path).exists():
            raise _404("file")
        mt = mimetypes.guess_type(d.file_name)[0] or "application/octet-stream"
        return FileResponse(d.stored_path, media_type=mt, filename=d.file_name)

    from .dashboard.routes import register as register_dashboard

    register_dashboard(app)

    if settings.lab:
        from .lab.app import create_lab_app

        app.mount("/lab", create_lab_app())

    return app
