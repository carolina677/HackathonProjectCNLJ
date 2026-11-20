import json
from datetime import datetime, timezone

from tests.brain.fakes import FakeJev
from tests.brain.test_heal import renamed
from tests.brain.test_induce import RecordingBackend
from tests.recipe.test_engine import ssr_recipe
from watchtower.judge.llm import LLM
from watchtower.models import PortalState, RunKind, Tender, TenderStatus
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
    await repo.mark_success(pid, 1, now, fill={"title": 1.0, "buyerName": 1.0, "publicationDate": 1.0,
                                               "submissionDeadline": 1.0})
    pl = Pipeline(repo, settings, HostLimiter(0.0), None, FakeJev(), None)
    result = await pl.read(pid, "run")
    p = await repo.get_portal(pid)
    ts = {t.id: t for t in await repo.list_tenders(pid)}
    assert str(result) == "failed" and p.state == PortalState.healing and "buyerName" in p.reason
    assert ts["LAB-0001"].title == "Original" and ts["LAB-0001"].buyerName == "Stadt"
    assert len(ts) == 23  # the 22 new tenders were served while the read ran


async def test_failed_portal_with_recipe_gets_another_repair(lab, repo, settings):
    pid = await repo.create_portal(lab.url("ssr"))
    await repo.set_recipe(pid, json.loads(ssr_recipe(lab).model_dump_json()))
    await repo.mark_success(pid, 23, datetime.now(timezone.utc))
    await repo.set_state(pid, PortalState.failed, "Could not repair automatically after 3 attempts")
    lab.mutate("ssr", "rename_classes")
    pl = Pipeline(repo, settings, HostLimiter(0.0), None, FakeJev(), None)
    result = await pl.read(pid, "run")
    p = await repo.get_portal(pid)
    assert str(result) == "failed" and p.state == PortalState.healing
    assert pl.pop_followup(pid) == (RunKind.repair, 0.0)


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


def _patch_all_503(monkeypatch, pl):
    real = pl.fetcher

    def down(p):
        f = real(p)
        get = f.get

        async def g(url, *a, **k):
            page = await get(url, *a, **k)
            page.status = 503
            return page

        f.get = g
        return f

    monkeypatch.setattr(pl, "fetcher", down)


async def test_outage_retries_read_instead_of_repair(lab, repo, settings, monkeypatch):
    pid, pl = await _served_ssr(lab, repo, settings, json.loads(ssr_recipe(lab).model_dump_json()))
    _patch_all_503(monkeypatch, pl)
    result = await pl.read(pid, "r2")
    p = await repo.get_portal(pid)
    assert str(result) == "failed" and p.state == PortalState.healing and "not reachable" in p.reason
    assert pl.pop_followup(pid) == (RunKind.read, 60.0)
    assert len(await repo.list_tenders(pid)) == 23


async def test_third_outage_in_a_row_goes_to_repair(lab, repo, settings, monkeypatch):
    pid, pl = await _served_ssr(lab, repo, settings, json.loads(ssr_recipe(lab).model_dump_json()))
    _patch_all_503(monkeypatch, pl)
    followups = []
    for i in range(3):
        await pl.read(pid, f"r{i + 2}")
        followups.append(pl.pop_followup(pid))
    assert followups == [(RunKind.read, 60.0), (RunKind.read, 300.0), (RunKind.repair, 0.0)]


async def test_recovered_portal_resets_outage_count(lab, repo, settings, monkeypatch):
    pid, pl = await _served_ssr(lab, repo, settings, json.loads(ssr_recipe(lab).model_dump_json()))
    real_fetcher = pl.fetcher
    _patch_all_503(monkeypatch, pl)
    await pl.read(pid, "r2")
    assert pl._outages.get(pid) == 1
    pl.pop_followup(pid)
    monkeypatch.setattr(pl, "fetcher", real_fetcher)
    assert str(await pl.read(pid, "r3")) == "ok"
    p = await repo.get_portal(pid)
    assert p.state == PortalState.serving and pl._outages.get(pid, 0) == 0


async def test_fill_drop_noticed_after_restart(lab, repo, settings):
    pid = await repo.create_portal(lab.url("ssr"))
    broken = json.loads(ssr_recipe(lab).model_dump_json())
    del broken["fields"]["buyerName"]
    await repo.set_recipe(pid, broken)
    await repo.set_state(pid, PortalState.serving, None)
    now = datetime.now(timezone.utc)
    await repo.upsert_tenders(pid, [Tender(id="LAB-0001", url="https://x/1", title="Original", buyerName="Stadt",
                                           publicationDate=None, submissionDeadline=None, status=TenderStatus.open,
                                           documents=[], lastChangedAt=now)], now)
    await repo.mark_success(pid, 1, now, fill={"title": 1.0, "buyerName": 1.0, "publicationDate": 1.0,
                                               "submissionDeadline": 1.0})
    fresh_pipeline = Pipeline(repo, settings, HostLimiter(0.0), None, FakeJev(), None)
    result = await fresh_pipeline.read(pid, "run")
    p = await repo.get_portal(pid)
    assert str(result) == "failed" and p.state == PortalState.healing and "buyerName" in p.reason


async def test_attach_delivers_documents_before_serving(lab, repo, settings):
    pid = await repo.create_portal(lab.url("ssr"))
    backend = RecordingBackend(json.loads(ssr_recipe(lab).model_dump_json()))
    pl = Pipeline(repo, settings, HostLimiter(0.0), None, FakeJev(), LLM(settings, backend=backend))
    assert str(await pl.attach(pid, "r1")) == "ok"
    p = await repo.get_portal(pid)
    ts = await repo.list_tenders(pid)
    assert p.state == PortalState.serving and len(ts) == 23
    assert all(len(t.documents) >= 1 for t in ts)
    assert pl.pop_followup(pid) is None


async def test_repair_delivers_refreshed_tenders_before_serving(lab, repo, settings):
    pid, _ = await _served_ssr(lab, repo, settings, json.loads(ssr_recipe(lab).model_dump_json()))
    lab.mutate("ssr", "rename_classes")
    pl = Pipeline(repo, settings, HostLimiter(0.0), None, FakeJev(),
                  LLM(settings, backend=RecordingBackend(renamed(lab))))
    assert str(await pl.read(pid, "r2")) == "failed"
    assert (await repo.get_portal(pid)).state == PortalState.healing
    assert pl.pop_followup(pid) == (RunKind.repair, 0.0)
    assert str(await pl.repair(pid, "rep1")) == "ok"
    p = await repo.get_portal(pid)
    assert p.state == PortalState.serving and p.recipe["item"] == "tr.vx-wor-rednet"
    assert len(await repo.list_tenders(pid)) == 23
    assert pl.pop_followup(pid) is None


async def test_pulse_uses_no_vision_and_list_fields_only(lab, repo, settings, monkeypatch):
    from watchtower import pipeline as pipeline_mod

    pid = await repo.create_portal(lab.url("ssr"))
    recipe = json.loads(ssr_recipe(lab).model_dump_json())
    await repo.set_recipe(pid, recipe)
    await repo.set_state(pid, PortalState.serving, None)
    await repo.mark_success(pid, 23, datetime.now(timezone.utc), fill={"title": 1.0})
    pl = Pipeline(repo, settings, HostLimiter(0.0), None, FakeJev(), None)
    real = pl.fetcher

    def with_vision(p):
        f = real(p)
        f.on_screenshot = lambda png, url: None  # what a real Jev client installs (paid locate calls)
        return f

    monkeypatch.setattr(pl, "fetcher", with_vision)
    seen = {}

    async def fake_run(recipe, fetcher, **kw):
        seen["vision"] = fetcher.on_screenshot
        await kw["on_list"]([{"id": "1", "title": "t"}], [])
        return []

    monkeypatch.setattr(pipeline_mod, "run_recipe", fake_run)
    assert await pl.pulse(pid) is True
    assert seen["vision"] is None
