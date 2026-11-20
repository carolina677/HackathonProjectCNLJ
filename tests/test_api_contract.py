from datetime import date, datetime, timedelta, timezone

from watchtower.models import Document, Tender, TenderStatus

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def _tender(i: str, changed: datetime) -> Tender:
    return Tender(id=i, url=f"https://x.de/t/{i}", title=f"T {i}", buyerName="Stadt",
                  publicationDate=date(2026, 11, 1), submissionDeadline=NOW + timedelta(days=10),
                  status=TenderStatus.open, documents=[], lastChangedAt=changed)


async def _portal(client, url="https://x.de"):
    return (await client.post("/portals", json={"url": url})).json()["id"]


async def test_post_portal_201_and_idempotent(client):
    r1 = await client.post("/portals", json={"url": "https://x.de/a/"})
    r2 = await client.post("/portals", json={"url": "https://x.de/a"})
    assert r1.status_code == 201 and r1.json()["id"] == r2.json()["id"]
    assert set(r1.json()) == {"id"}


async def test_unknown_id_404(client):
    for m, p in [("GET", "/portals/nope"), ("DELETE", "/portals/nope"), ("POST", "/portals/nope/refresh"),
                 ("GET", "/portals/nope/tenders"), ("GET", "/portals/nope/runs"), ("GET", "/files/nope")]:
        r = await client.request(m, p)
        assert r.status_code == 404, (m, p)
        assert "error" in r.json()


async def test_portal_entry_shape(client):
    pid = await _portal(client)
    e = (await client.get(f"/portals/{pid}")).json()
    assert set(e) == {"id", "url", "state", "reason", "tenderCount", "lastSuccessAt"}
    assert e["state"] == "integrating" and e["lastSuccessAt"] is None and e["tenderCount"] == 0
    lst = (await client.get("/portals")).json()
    assert isinstance(lst, list) and lst[0]["id"] == pid


async def test_refresh_202_returns_run_id(client):
    pid = await _portal(client)
    r = await client.post(f"/portals/{pid}/refresh")
    assert r.status_code == 202 and isinstance(r.json()["runId"], str)


async def test_delete_204(client):
    pid = await _portal(client)
    r = await client.delete(f"/portals/{pid}")
    assert r.status_code == 204
    assert (await client.get(f"/portals/{pid}")).status_code == 404
    assert all(p["id"] != pid for p in (await client.get("/portals")).json())


async def test_changed_since_filters(client, repo):
    pid = await _portal(client)
    t1, t2 = NOW - timedelta(hours=2), NOW
    await repo.upsert_tenders(pid, [_tender("a", t1), _tender("b", t2)], now=None)
    allt = (await client.get(f"/portals/{pid}/tenders")).json()
    assert {t["id"] for t in allt} == {"a", "b"}
    since = (NOW - timedelta(hours=1)).isoformat()
    some = (await client.get(f"/portals/{pid}/tenders", params={"changedSince": since})).json()
    assert [t["id"] for t in some] == ["b"]
    assert (await client.get(f"/portals/{pid}")).json()["tenderCount"] == 2


async def test_upsert_bumps_last_changed_only_on_change(client, repo):
    pid = await _portal(client)
    t0 = NOW - timedelta(days=1)
    await repo.upsert_tenders(pid, [_tender("a", t0)], now=t0)
    changed = await repo.upsert_tenders(pid, [_tender("a", t0)], now=NOW)
    assert changed == 0
    assert (await repo.list_tenders(pid))[0].lastChangedAt == t0
    new = _tender("a", t0).model_copy(update={"title": "Changed"})
    assert await repo.upsert_tenders(pid, [new], now=NOW) == 1
    assert (await repo.list_tenders(pid))[0].lastChangedAt == NOW


async def test_files_returns_bytes(client, repo, tmp_path):
    f = tmp_path / "blob"
    f.write_bytes(b"%PDF-1.4 hi")
    fid = await repo.save_document(portal_id="p", tender_id="t", file_name="Unterlagen.pdf",
                                   size_bytes=11, sha256="ab", stored_path=str(f))
    r = await client.get(f"/files/{fid}")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 hi"
    assert "Unterlagen.pdf" in r.headers["content-disposition"]
    assert r.headers["content-type"].startswith("application/pdf")


async def test_runs_shape(client, repo):
    pid = await _portal(client)
    rid = await repo.start_run(pid, "read")
    runs = (await client.get(f"/portals/{pid}/runs")).json()
    run = next(r for r in runs if r["id"] == rid)
    assert set(run) == {"id", "kind", "startedAt", "finishedAt", "result"}
    assert run["kind"] == "read" and run["finishedAt"] is None and run["result"] is None
    await repo.finish_run(rid, "ok")
    run = next(r for r in (await client.get(f"/portals/{pid}/runs")).json() if r["id"] == rid)
    assert run["result"] == "ok" and run["finishedAt"].endswith("+00:00")


async def test_documents_in_tender(client, repo):
    pid = await _portal(client)
    t = _tender("a", NOW).model_copy(update={"documents": [Document(fileName="a.pdf", sizeBytes=1, sha256="x", path="/files/1")]})
    await repo.upsert_tenders(pid, [t], now=NOW)
    got = (await client.get(f"/portals/{pid}/tenders")).json()[0]
    assert got["documents"][0]["path"] == "/files/1"
