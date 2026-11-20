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


def retry_every(settings: Settings, state: PortalState) -> int:
    """Seconds between scheduled reads. Failed portals wait longer (6x), but never more than a day."""
    interval = settings.read_interval_seconds
    if state == PortalState.failed:
        return max(interval, min(interval * 6, 24 * 3600))
    return interval


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
    def __init__(self, repo: Repo, pipeline: Runner, settings: Settings, max_parallel: int = 8,
                 tick: float | None = None):
        self.repo, self.pipeline, self.settings = repo, pipeline, settings
        self.tick = tick if tick is not None else getattr(settings, "scheduler_tick_seconds", 5.0)
        self._last_pulse: dict[str, float] = {}
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
        """Wait until the portal has no run in flight, follow-up runs included."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while (t := self._tasks.get(pid)) is not None and not t.done():
            await asyncio.wait_for(asyncio.shield(t), max(0.01, end - loop.time()))

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
            kind_next, delay = nxt if isinstance(nxt, tuple) else (nxt, 0.0)
            if delay > 0:
                await asyncio.sleep(delay)
            if not self.is_cancelled(pid):
                self._tasks.pop(pid, None)
                await self.enqueue(pid, kind_next)

    async def _finish(self, rid: str, pid: str, result: RunResult, detail: str | None) -> None:
        try:
            await self.repo.finish_run(rid, result, detail)
        finally:
            events.emit(pid, "run", {"id": rid, "status": "finished", "result": str(result), "detail": detail})

    async def _periodic(self) -> None:
        while True:
            await asyncio.sleep(self.tick)
            now = time.monotonic()
            for p in await self.repo.list_portals():
                if p.id in self._tasks and not self._tasks[p.id].done():
                    continue
                every = retry_every(self.settings, p.state)
                last = self._last_enqueued.get(p.id)
                if last is None:
                    due = now - self._started >= self.offset(p.id) % every
                else:
                    due = now - last >= every
                if due:
                    await self.enqueue(p.id, RunKind.read)
                elif self._pulse_due(p, now):
                    self._last_pulse[p.id] = now
                    self._tasks[p.id] = asyncio.create_task(self._pulse(p.id))

    def _pulse_due(self, p, now: float) -> bool:
        """A cheap list-only check between full reads, only for portals that claim to deliver."""
        every = getattr(self.settings, "pulse_interval_seconds", 0)
        if every <= 0 or p.state != PortalState.serving or self.is_cancelled(p.id):
            return False
        if getattr(self.pipeline, "pulse", None) is None:
            return False
        last = max(self._last_enqueued.get(p.id, self._started), self._last_pulse.get(p.id, 0.0))
        return now - last >= every

    async def _pulse(self, pid: str) -> None:
        """Not a run: it does not show in /runs. A failed pulse starts a full read, which decides the state."""
        try:
            ok = await self.pipeline.pulse(pid)
        except Exception:
            log.exception("pulse for %s crashed", pid)
            return
        if not ok and not self.is_cancelled(pid):
            if self._tasks.get(pid) is asyncio.current_task():
                self._tasks.pop(pid, None)
            await self.enqueue(pid, RunKind.read)

    async def _clock(self) -> None:
        while True:
            try:
                n = await self.repo.close_expired()
                if n:
                    events.emit(None, "log", {"msg": f"Closed {n} tenders whose deadline passed"})
            except Exception:
                log.exception("clock job failed")
            await asyncio.sleep(60)
