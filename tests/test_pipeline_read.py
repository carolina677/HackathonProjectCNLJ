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
