"""Background work: per-host politeness, one run at a time per portal, periodic reads, clock job."""

import asyncio
import hashlib
import logging
import time
from typing import Protocol

from . import events
from .config import Settings
from .db import Repo
from .models import PortalState, RunKind, RunResult

log = logging.getLogger("watchtower.scheduler")


class HostLimiter:
    """At most one request per second per host (brief: be polite to real portals)."""

    def __init__(self, min_interval: float = 1.0):
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host)
            if last is not None:
                wait = last + self.min_interval - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last[host] = time.monotonic()


class Runner(Protocol):
    async def run(self, pid: str, run_id: str, kind: RunKind) -> RunResult: ...


class Scheduler:
    def __init__(self, repo: Repo, pipeline: Runner, settings: Settings, max_parallel: int = 8):
        self.repo, self.pipeline, self.settings = repo, pipeline, settings
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._last_enqueued: dict[str, float] = {}
        self._sem = asyncio.Semaphore(max_parallel)
        self._loops: list[asyncio.Task] = []
        self._started = time.monotonic()

    # --- public --------------------------------------------------------
    def is_cancelled(self, pid: str) -> bool:
        return pid in self._cancelled

    def offset(self, pid: str) -> int:
        return int(hashlib.sha1(pid.encode()).hexdigest(), 16) % max(1, self.settings.read_interval_seconds)

    async def enqueue(self, pid: str, kind: RunKind = RunKind.read) -> str:
        self._cancelled.discard(pid)
        running = await self.repo.running_run(pid)
        if running and pid in self._tasks and not self._tasks[pid].done():
            return running
        rid = await self.repo.start_run(pid, kind)
        self._last_enqueued[pid] = time.monotonic()
        events.emit(pid, "run", {"id": rid, "kind": str(kind), "status": "started"})
        self._tasks[pid] = asyncio.create_task(self._execute(pid, rid, kind))
        return rid

    async def cancel(self, pid: str) -> None:
        self._cancelled.add(pid)
        t = self._tasks.pop(pid, None)
        if t and not t.done():
            t.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(t), timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

    async def wait_idle(self, pid: str, timeout: float = 60) -> None:
        t = self._tasks.get(pid)
        if t:
            await asyncio.wait_for(asyncio.shield(t), timeout)

    def start(self) -> None:
        self._loops = [asyncio.create_task(self._periodic()), asyncio.create_task(self._clock())]

    async def stop(self) -> None:
        for t in [*self._loops, *self._tasks.values()]:
            t.cancel()
        for t in [*self._loops, *self._tasks.values()]:
            try:
                await t
            except BaseException:
                pass
        self._loops, self._tasks = [], {}

    # --- internals -----------------------------------------------------
    async def _execute(self, pid: str, rid: str, kind: RunKind) -> None:
        result, detail = RunResult.failed, None
        try:
            async with self._sem:
                result = await self.pipeline.run(pid, rid, kind)
        except asyncio.CancelledError:
            detail = "cancelled"
            await self._finish(rid, pid, RunResult.failed, detail)
            raise
        except Exception as e:  # a crashing run must never take the scheduler down
            log.exception("run %s for %s crashed", rid, pid)
            detail = f"{type(e).__name__}: {e}"
        await self._finish(rid, pid, result, detail)
        follow = getattr(self.pipeline, "pop_followup", None)
        nxt = follow(pid) if follow else None
        if nxt and not self.is_cancelled(pid):
            self._tasks.pop(pid, None)
            await self.enqueue(pid, nxt)

    async def _finish(self, rid: str, pid: str, result: RunResult, detail: str | None) -> None:
        try:
            await self.repo.finish_run(rid, result, detail)
        finally:
            events.emit(pid, "run", {"id": rid, "status": "finished", "result": str(result), "detail": detail})

    async def _periodic(self) -> None:
        interval = self.settings.read_interval_seconds
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            for p in await self.repo.list_portals():
                if p.id in self._tasks and not self._tasks[p.id].done():
                    continue
                every = interval * (6 if p.state == PortalState.failed else 1)
                last = self._last_enqueued.get(p.id)
                if last is None:
                    due = now - self._started >= self.offset(p.id) % every
                else:
                    due = now - last >= every
                if due:
                    await self.enqueue(p.id, RunKind.read)

    async def _clock(self) -> None:
        while True:
            try:
                n = await self.repo.close_expired()
                if n:
                    events.emit(None, "log", {"msg": f"Closed {n} tenders whose deadline passed"})
            except Exception:
                log.exception("clock job failed")
            await asyncio.sleep(60)
