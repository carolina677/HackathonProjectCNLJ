"""End to end: real app + scheduler + fetcher against the Chaos Lab. Judge and LLM replay recorded answers."""

import asyncio
import json

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


@pytest.fixture
async def e2e(lab, settings, monkeypatch):
    from watchtower import pipeline
    from watchtower.app import create_app
    from watchtower.fetch import close_browser

    replay = ReplayLLM(lab)
    monkeypatch.setattr(pipeline, "make_judges", lambda s: (FakeJev(), LLM(s, backend=replay)))
    s = settings.model_copy(update={"min_request_interval": 0.02})
    app = create_app(s, start_scheduler=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c, lab, replay, app
    await close_browser()


async def until(c, pid, pred, timeout=60.0):
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        p = (await c.get(f"/portals/{pid}")).json()
        if pred(p):
            return p
        await asyncio.sleep(0.2)
    raise AssertionError(f"timeout; last state {p}")


async def idle(app, pid, timeout=60):
    await app.state.scheduler.wait_idle(pid, timeout)


async def _attach(c, url):
    return (await c.post("/portals", json={"url": url})).json()["id"]


async def test_attach_ssr_reaches_serving(e2e):
    c, lab, _, app = e2e
    pid = await _attach(c, lab.url("ssr"))
    await until(c, pid, lambda p: p["state"] == "serving")
    await until(c, pid, lambda p: p["tenderCount"] == 23)
    await idle(app, pid)
    ts = (await c.get(f"/portals/{pid}/tenders")).json()
    s = score(ts, lab.truth("ssr"))
    assert s.attach == 1.0 and s.quality >= 0.95, s.wrong_examples
    assert s.field_accuracy["documents"] == 1.0
    runs = (await c.get(f"/portals/{pid}/runs")).json()
    assert runs[-1]["kind"] == "read" and all(r["result"] == "ok" for r in runs)
    assert (await c.get(f"/portals/{pid}")).json()["lastSuccessAt"] is not None


async def test_spa_attaches_via_json_api(e2e):
    c, lab, _, app = e2e
    pid = await _attach(c, lab.url("spa"))
    await until(c, pid, lambda p: p["state"] == "serving" and p["tenderCount"] == 23, 90)
    await idle(app, pid)
    s = score((await c.get(f"/portals/{pid}/tenders")).json(), lab.truth("spa"))
    assert s.attach == 1.0 and s.wrong_values == 0, s.wrong_examples


async def test_multistep_zip_documents(e2e):
    c, lab, _, app = e2e
    pid = await _attach(c, lab.url("multistep"))
    await until(c, pid, lambda p: p["state"] == "serving" and p["tenderCount"] == 23, 90)
    await idle(app, pid)
    s = score((await c.get(f"/portals/{pid}/tenders")).json(), lab.truth("multistep"))
    assert s.field_accuracy["documents"] == 1.0


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
    lab.mutate("ssr", "rename_classes")
    await c.post(f"/portals/{pid}/refresh")
    await until(c, pid, lambda p: p["state"] == "healing")
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
