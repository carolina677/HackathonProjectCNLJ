import asyncio
import json

from watchtower import events
from watchtower.models import PortalState, RunKind, RunResult


async def test_index_lists_portals_with_state_and_story(client, repo):
    a = await repo.create_portal("https://a.example/ausschreibungen")
    b = await repo.create_portal("https://b.example/tenders")
    await repo.set_state(b, PortalState.failed, "Portal requires a login")
    rid = await repo.start_run(a, RunKind.repair)
    await repo.finish_run(rid, RunResult.ok)
    r = await client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    html = r.text
    assert "https://a.example/ausschreibungen" in html and "https://b.example/tenders" in html
    assert "Portal requires a login" in html
    assert "state-failed" in html and "state-integrating" in html
    assert "Repaired" in html  # last runs told in words
    assert 'action="/portals"' in html or "hx-post=\"/portals\"" in html
    assert "data-tenders" in html and 'id="tenders-dialog"' in html and "Briefly" in html and "Watchtower" in html


async def test_portal_card_fragment(client, repo):
    a = await repo.create_portal("https://a.example/")
    r = await client.get(f"/ui/portals/{a}/card")
    assert r.status_code == 200 and f'id="card-{a}"' in r.text
    assert (await client.get("/ui/portals/nope/card")).status_code == 404


async def test_events_stream_delivers_emitted_event(client):
    async def later():
        await asyncio.sleep(0.2)
        events.emit("p1", "log", {"msg": "hello theater"})

    task = asyncio.create_task(later())
    r = await asyncio.wait_for(client.get("/events", params={"limit": 1}), 2)
    await task
    assert r.headers["content-type"].startswith("text/event-stream")
    data = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    assert data[0]["kind"] == "log" and data[0]["data"]["msg"] == "hello theater"


async def test_static_assets_served(client):
    assert (await client.get("/static/app.css")).status_code == 200
    assert (await client.get("/static/app.js")).status_code == 200


async def test_static_urls_carry_a_version_so_updates_are_not_cached(client):
    import re

    html = (await client.get("/")).text
    assert re.search(r'/static/app\.css\?v=\w+', html) and re.search(r'/static/app\.js\?v=\w+', html)


async def test_ui_summary_endpoint(client, repo):
    from datetime import date, datetime, timezone
    from watchtower.models import Tender, TenderStatus

    pid = await repo.create_portal("https://sum.example/")
    await repo.set_state(pid, PortalState.serving, None)
    now = datetime.now(timezone.utc)
    await repo.upsert_tenders(pid, [Tender(
        id="1", url="https://sum.example/1", title="T", buyerName="B",
        publicationDate=date(2026, 11, 20), submissionDeadline=None,
        status=TenderStatus.open, documents=[], lastChangedAt=now,
    )], now=now)
    r = await client.get("/ui/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["serving"] == 1 and data["tenders"] == 1
    assert {"healing", "integrating", "failed", "avg_usd", "projected", "lab", "activity"} <= set(data)
    assert data["activity"]["total"] >= 1 and len(data["activity"]["days"]) == 7


async def test_ui_tenders_endpoint(client, repo):
    from datetime import date, datetime, timezone
    from watchtower.models import Tender, TenderStatus

    pid = await repo.create_portal("https://tenders.example/de")
    now = datetime.now(timezone.utc)
    await repo.upsert_tenders(pid, [Tender(
        id="t-1", url="https://tenders.example/de/1", title="Cloud Migration", buyerName="City Hall",
        publicationDate=date(2026, 11, 20), submissionDeadline=None,
        status=TenderStatus.open, documents=[], lastChangedAt=now,
    )], now=now)
    r = await client.get("/ui/tenders")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and len(body["tenders"]) == 1
    t = body["tenders"][0]
    assert t["portalId"] == pid and t["portalHost"] == "tenders.example/de" and t["title"] == "Cloud Migration"
