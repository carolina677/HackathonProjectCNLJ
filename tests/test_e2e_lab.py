"""End to end: real app + scheduler + fetcher against the Chaos Lab. Judge and LLM replay recorded answers."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from tests.brain.fakes import FakeJev
from tests.brain.test_heal import renamed
from tests.recipe.test_engine import spa_recipe, ssr_recipe
from watchtower.judge.llm import LLM
from watchtower.lab.scorer import score


class ReplayLLM:
    """Answers like a good LLM would for the lab portal named in the prompt."""

    def __init__(self, lab, delay: float = 0.0):
        self.lab, self.delay, self.calls = lab, delay, 0

    async def __call__(self, system, user, schema):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if "vx-wor-rednet" in user:
            r = renamed(self.lab)
        elif "api/tenders" in user:
            r = json.loads(spa_recipe(self.lab).model_dump_json())
        else:
            slug = next((s for s in ("multistep", "captcha-open-docs", "ssr") if f"/lab/{s}/" in user), "ssr")
            docs = ([{"selector": "a.tab", "attr": "href", "follow": True}, {"selector": "a.download", "attr": "href"}]
                    if slug == "multistep" else None)
            r = json.loads(ssr_recipe(self.lab, slug, docs=docs).model_dump_json())
        return r, 20000, 1500


@asynccontextmanager
async def _e2e_app(lab, settings, monkeypatch, **update):
    from watchtower import pipeline
    from watchtower.app import create_app
    from watchtower.fetch import close_browser

    replay = ReplayLLM(lab)
    monkeypatch.setattr(pipeline, "make_judges", lambda s: (FakeJev(), LLM(s, backend=replay)))
    s = settings.model_copy(update={"min_request_interval": 0.02, **update})
    app = create_app(s, start_scheduler=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c, lab, replay, app
    await close_browser()


@pytest.fixture
async def e2e(lab, settings, monkeypatch):
    async with _e2e_app(lab, settings, monkeypatch) as env:
        yield env


@pytest.fixture
async def e2e_pulse(lab, settings, monkeypatch):
    async with _e2e_app(lab, settings, monkeypatch, pulse_interval_seconds=1, scheduler_tick_seconds=0.1) as env:
        yield env


async def until(c, pid, pred, timeout=60.0):
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        p = (await c.get(f"/portals/{pid}")).json()
        if pred(p):
            return p
        await asyncio.sleep(0.2)
    raise AssertionError(f"timeout; last state {p}")


async def went_healing(pid, since: str, timeout=30.0) -> dict:
    """The healing state can be brief against the unthrottled local lab, so look for the state event, not a poll."""
    from watchtower import events

    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        hits = [e for e in events.history(pid, {"state"}) if e["ts"] > since and e["data"].get("to") == "healing"]
        if hits:
            return hits[0]["data"]
        await asyncio.sleep(0.1)
    raise AssertionError("never went healing")


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


async def idle(app, pid, timeout=60):
    await app.state.scheduler.wait_idle(pid, timeout)


async def _attach(c, url):
    return (await c.post("/portals", json={"url": url})).json()["id"]


async def test_attach_ssr_reaches_serving(e2e):
    c, lab, _, app = e2e
    pid = await _attach(c, lab.url("ssr"))
    await until(c, pid, lambda p: p["state"] == "serving")
    ts = (await c.get(f"/portals/{pid}/tenders")).json()
    s = score(ts, lab.truth("ssr"))
    assert s.attach == 1.0 and s.quality >= 0.95, s.wrong_examples
    assert s.field_accuracy["documents"] == 1.0
    await idle(app, pid)
    runs = (await c.get(f"/portals/{pid}/runs")).json()
    assert runs[-1]["kind"] == "read" and all(r["result"] == "ok" for r in runs)
    assert (await c.get(f"/portals/{pid}")).json()["lastSuccessAt"] is not None


async def test_spa_attaches_via_json_api(e2e):
    c, lab, _, app = e2e
    pid = await _attach(c, lab.url("spa"))
    await until(c, pid, lambda p: p["state"] == "serving", 90)
    s = score((await c.get(f"/portals/{pid}/tenders")).json(), lab.truth("spa"))
    assert s.attach == 1.0 and s.wrong_values == 0 and s.field_accuracy["documents"] == 1.0, s.wrong_examples
    await idle(app, pid)


async def test_multistep_zip_documents(e2e):
    c, lab, _, app = e2e
    pid = await _attach(c, lab.url("multistep"))
    await until(c, pid, lambda p: p["state"] == "serving", 90)
    s = score((await c.get(f"/portals/{pid}/tenders")).json(), lab.truth("multistep"))
    assert s.attach == 1.0 and s.field_accuracy["documents"] == 1.0
    await idle(app, pid)


async def test_login_portal_failed_with_reason(e2e):
    c, lab, _, _ = e2e
    pid = await _attach(c, lab.url("login"))
    p = await until(c, pid, lambda p: p["state"] == "failed")
    assert "login" in p["reason"].lower()


async def test_mutation_heals_and_keeps_tenders_while_healing(e2e):
    c, lab, replay, app = e2e
    pid = await _attach(c, lab.url("ssr"))
    await until(c, pid, lambda p: p["state"] == "serving" and p["tenderCount"] == 23)
    await idle(app, pid)
    before = (await c.get(f"/portals/{pid}/tenders")).json()
    lab.mutate("ssr", "rename_classes")
    replay.delay = 1.5
    await c.post(f"/portals/{pid}/refresh")
    p = await until(c, pid, lambda p: p["state"] == "healing")
    assert p["reason"]
    assert (await c.get(f"/portals/{pid}/tenders")).json() == before
    await until(c, pid, lambda p: p["state"] == "serving", 90)
    await idle(app, pid)
    runs = (await c.get(f"/portals/{pid}/runs")).json()
    assert any(r["kind"] == "repair" and r["result"] == "ok" for r in runs)
    assert score((await c.get(f"/portals/{pid}/tenders")).json(), lab.truth("ssr")).quality >= 0.95


async def test_read_makes_no_llm_call(e2e):
    c, lab, replay, app = e2e
    pid = await _attach(c, lab.url("ssr"))
    await until(c, pid, lambda p: p["state"] == "serving")
    await idle(app, pid)
    n = replay.calls
    rid = (await c.post(f"/portals/{pid}/refresh")).json()["runId"]
    await idle(app, pid)
    run = next(r for r in (await c.get(f"/portals/{pid}/runs")).json() if r["id"] == rid)
    assert run["result"] == "ok" and replay.calls == n


async def test_withdrawn_and_closed_reflected(e2e):
    c, lab, _, app = e2e
    pid = await _attach(c, lab.url("ssr"))
    await until(c, pid, lambda p: p["state"] == "serving" and p["tenderCount"] == 23)
    await idle(app, pid)
    lab.mutate("ssr", "withdraw_one")
    await c.post(f"/portals/{pid}/refresh")
    await idle(app, pid)
    ts = (await c.get(f"/portals/{pid}/tenders")).json()
    assert sum(t["status"] == "withdrawn" for t in ts) == 1


async def test_delete_stops_work(e2e):
    c, lab, _, app = e2e
    pid = await _attach(c, lab.url("ssr"))
    await asyncio.sleep(0.5)
    assert (await c.delete(f"/portals/{pid}")).status_code == 204
    await asyncio.sleep(1)
    assert pid not in app.state.scheduler._tasks or app.state.scheduler._tasks[pid].done()


async def test_repair_keeps_before_and_after_view(e2e):
    c, lab, replay, app = e2e
    pid = await _attach(c, lab.url("ssr"))
    await until(c, pid, lambda p: p["state"] == "serving" and p["tenderCount"] == 23)
    await idle(app, pid)
    since = now_iso()
    lab.mutate("ssr", "rename_classes")
    await c.post(f"/portals/{pid}/refresh")
    await went_healing(pid, since)
    await until(c, pid, lambda p: p["state"] == "serving", 90)
    await idle(app, pid)
    reps = (await c.get(f"/portals/{pid}/repairs")).json()
    assert len(reps) == 1 and reps[0]["ok"] and reps[0]["diff"]
    r = reps[0]
    assert r["before"]["item"] == "tr.tender-row" and r["after"]["item"] == "tr.vx-wor-rednet"
    assert r["before"]["items"] >= 10 and r["after"]["items"] >= 10      # each recipe reads its own page
    assert r["before"]["changed"] >= 1 and r["after"]["changed"] >= 1   # renamed structure shows on both sides
    before = (await c.get(f"/portals/{pid}/repairs/1/before.html")).text
    after = (await c.get(f"/portals/{pid}/repairs/1/after.html")).text
    assert "wt-gone" in before and "wt-new" in after and after.count("wt-item") >= 10


async def test_pulse_notices_break_without_refresh(e2e_pulse):
    c, lab, replay, app = e2e_pulse
    pid = await _attach(c, lab.url("ssr"))
    await until(c, pid, lambda p: p["state"] == "serving" and p["tenderCount"] == 23)
    await idle(app, pid)
    n = replay.calls
    assert await app.state.pipeline.pulse(pid) is True and replay.calls == n  # a healthy pulse costs no model call
    since = now_iso()
    lab.mutate("ssr", "rename_classes")
    healing = await went_healing(pid, since)  # nobody pressed refresh
    assert healing["reason"]
    await until(c, pid, lambda p: p["state"] == "serving", 90)
    await idle(app, pid)
    runs = (await c.get(f"/portals/{pid}/runs")).json()
    assert any(r["kind"] == "repair" and r["result"] == "ok" for r in runs)


async def test_portal_whose_documents_cannot_be_found_serves_tenders_and_says_so(lab, settings, monkeypatch):
    """Fields verified but no document links after 3 attempts: serve the tenders, flag missing documents."""
    from tests.recipe.test_engine import ssr_recipe as real

    monkeypatch.setattr(f"{__name__}.ssr_recipe",
                        lambda lab_, slug="ssr", docs=None: real(lab_, slug, docs=[{"selector": "ul.nope a"}]))
    async with _e2e_app(lab, settings, monkeypatch) as (c, lab_, replay, app):
        pid = (await c.post("/portals", json={"url": lab.url("ssr")})).json()["id"]
        p = await until(c, pid, lambda p: p["state"] in ("serving", "failed"), timeout=120)
        assert p["state"] == "serving" and p["tenderCount"] == 23
        assert "without documents" in (p["reason"] or "")
        assert replay.calls == 3  # it tried to find the documents first
        await c.post(f"/portals/{pid}/refresh")
        await idle(app, pid)
        assert "without documents" in ((await c.get(f"/portals/{pid}")).json()["reason"] or "")
