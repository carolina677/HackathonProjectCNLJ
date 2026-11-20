import asyncio
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from watchtower.models import PortalState, RunKind, RunResult, Tender, TenderStatus
from watchtower.pipeline import Pipeline
from watchtower.scheduler import HostLimiter, Scheduler


async def test_limiter_one_per_second():
    lim = HostLimiter()
    t0 = time.monotonic()
    for _ in range(3):
        await lim.acquire("a.de")
    assert time.monotonic() - t0 >= 2.0


async def test_limiter_hosts_independent():
    lim = HostLimiter()
    t0 = time.monotonic()
    await lim.acquire("a.de")
    await lim.acquire("b.de")
    assert time.monotonic() - t0 < 0.5


class SlowPipeline:
    def __init__(self, sched_ref, delay=1.0):
        self.delay, self.calls, self.sched_ref = delay, 0, sched_ref

    async def run(self, pid, run_id, kind):
        while True:
            if self.sched_ref[0].is_cancelled(pid):
                return RunResult.failed
            self.calls += 1
            await asyncio.sleep(0.05)
            if self.calls * 0.05 >= self.delay:
                return RunResult.ok


@pytest.fixture
async def sched_env(repo, settings):
    ref = [None]
    pipe = SlowPipeline(ref)
    s = Scheduler(repo, pipe, settings)
    ref[0] = s
    pid = await repo.create_portal("https://x.de")
    yield s, pipe, pid
    await s.stop()


async def test_refresh_during_read_returns_same_run(sched_env):
    s, _, pid = sched_env
    r1 = await s.enqueue(pid, RunKind.read)
    r2 = await s.enqueue(pid, RunKind.read)
    assert r1 == r2


async def test_run_finishes_with_result(sched_env, repo):
    s, pipe, pid = sched_env
    pipe.delay = 0.1
    rid = await s.enqueue(pid, RunKind.read)
    await s.wait_idle(pid, timeout=3)
    run = next(r for r in await repo.list_runs(pid) if r.id == rid)
    assert run.result == RunResult.ok and run.finishedAt is not None


async def test_delete_stops_calls(sched_env):
    s, pipe, pid = sched_env
    pipe.delay = 30
    await s.enqueue(pid, RunKind.read)
    await asyncio.sleep(0.2)
    await s.cancel(pid)
    n = pipe.calls
    await asyncio.sleep(0.5)
    assert pipe.calls == n


async def test_clock_closes_expired(repo):
    pid = await repo.create_portal("https://x.de")
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    t = Tender(id="a", url="u", title="t", buyerName="b", publicationDate=date(2026, 1, 1),
               submissionDeadline=past, status=TenderStatus.open, documents=[],
               lastChangedAt=past - timedelta(days=1))
    await repo.upsert_tenders(pid, [t], now=past - timedelta(days=1))
    assert await repo.close_expired() == 1
    got = (await repo.list_tenders(pid))[0]
    assert got.status == TenderStatus.closed and got.lastChangedAt > past


def test_illegal_transition_raises():
    with pytest.raises(ValueError):
        Pipeline.check_transition(PortalState.failed, PortalState.healing)
    Pipeline.check_transition(PortalState.serving, PortalState.healing)
    Pipeline.check_transition(PortalState.healing, PortalState.serving)
    Pipeline.check_transition(PortalState.failed, PortalState.integrating)


async def test_due_portals_staggered(sched_env, settings):
    s, _, pid = sched_env
    assert 0 <= s.offset(pid) < settings.read_interval_seconds
