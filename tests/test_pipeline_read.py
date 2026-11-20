import json
from datetime import datetime, timezone

from tests.brain.fakes import FakeJev
from tests.recipe.test_engine import ssr_recipe
from watchtower.models import PortalState, Tender, TenderStatus
from watchtower.pipeline import Pipeline
from watchtower.scheduler import HostLimiter


async def test_failed_read_keeps_served_tenders_but_adds_new_ones(lab, repo, settings):
    pid = await repo.create_portal(lab.url("ssr"))
    broken = json.loads(ssr_recipe(lab).model_dump_json())
    del broken["fields"]["buyerName"]  # buyer suddenly empty everywhere → unhealthy at the end of the read
    await repo.set_recipe(pid, broken)
    await repo.set_state(pid, PortalState.serving, None)
    now = datetime.now(timezone.utc)
    await repo.upsert_tenders(pid, [Tender(id="LAB-0001", url="https://x/1", title="Original", buyerName="Stadt",
                                           publicationDate=None, submissionDeadline=None, status=TenderStatus.open,
                                           documents=[], lastChangedAt=now)], now)
    await repo.mark_success(pid, 1, now)
    pl = Pipeline(repo, settings, HostLimiter(0.0), None, FakeJev(), None)
    pl._prev_fill[pid] = {"title": 1.0, "buyerName": 1.0, "publicationDate": 1.0, "submissionDeadline": 1.0}
    result = await pl.read(pid, "run")
    p = await repo.get_portal(pid)
    ts = {t.id: t for t in await repo.list_tenders(pid)}
    assert str(result) == "failed" and p.state == PortalState.healing and "buyerName" in p.reason
    assert ts["LAB-0001"].title == "Original" and ts["LAB-0001"].buyerName == "Stadt"
    assert len(ts) == 23  # the 22 new tenders were served while the read ran


async def test_failed_portal_with_recipe_gets_another_repair(lab, repo, settings):
    from watchtower.models import RunKind

    pid = await repo.create_portal(lab.url("ssr"))
    await repo.set_recipe(pid, json.loads(ssr_recipe(lab).model_dump_json()))
    await repo.mark_success(pid, 23, datetime.now(timezone.utc))
    await repo.set_state(pid, PortalState.failed, "Could not repair automatically after 3 attempts")
    lab.mutate("ssr", "rename_classes")
    pl = Pipeline(repo, settings, HostLimiter(0.0), None, FakeJev(), None)
    result = await pl.read(pid, "run")
    p = await repo.get_portal(pid)
    assert str(result) == "failed" and p.state == PortalState.healing
    assert pl.pop_followup(pid) == RunKind.repair


async def _served_ssr(lab, repo, settings, recipe):
    pid = await repo.create_portal(lab.url("ssr"))
    await repo.set_recipe(pid, recipe)
    await repo.set_state(pid, PortalState.serving, None)
    pl = Pipeline(repo, settings, HostLimiter(0.0), None, FakeJev(), None)
    assert str(await pl.read(pid, "r1")) == "ok"
    assert len(await repo.list_tenders(pid)) == 23
    return pid, pl


async def test_list_cut_at_max_pages_closes_nothing(lab, repo, settings):
    r = json.loads(ssr_recipe(lab).model_dump_json())
    pid, pl = await _served_ssr(lab, repo, settings, r)
    r["pagination"]["max_pages"] = 2
    await repo.set_recipe(pid, r)
    await pl.read(pid, "r2")
    assert all(t.status == TenderStatus.open for t in await repo.list_tenders(pid))


async def test_http_error_mid_list_closes_nothing(lab, repo, settings, monkeypatch):
    pid, pl = await _served_ssr(lab, repo, settings, json.loads(ssr_recipe(lab).model_dump_json()))
    real = pl.fetcher

    def flaky(p):
        f = real(p)
        get = f.get

        async def g(url, *a, **k):
            page = await get(url, *a, **k)
            if "page=3" in url:
                page.status = 503
            return page
        f.get = g
        return f
    monkeypatch.setattr(pl, "fetcher", flaky)
    await pl.read(pid, "r2")
    assert all(t.status == TenderStatus.open for t in await repo.list_tenders(pid))
