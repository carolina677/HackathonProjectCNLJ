import asyncio

from tests.brain.fakes import FakeJev
from watchtower import events
from watchtower.judge.jev import Box
from watchtower.judge.llm import LLM


class FakeLLMBackend:
    async def __call__(self, system, user, schema):
        await asyncio.sleep(0.05)
        return {"choice": "tender_list"}, 12000, 20


async def test_theater_page(client, repo):
    pid = await repo.create_portal("https://portal.example/list")
    r = await client.get(f"/portals/{pid}/theater")
    assert r.status_code == 200 and "theater.js" in r.text and pid in r.text
    assert "data-break" not in r.text  # not a lab portal
    assert (await client.get("/portals/nope/theater")).status_code == 404


async def test_break_button_only_for_lab(client, repo, lab):
    pid = await repo.create_portal(lab.url("ssr"))
    assert "data-break" in (await client.get(f"/portals/{pid}/theater")).text
    assert "data-break" in (await client.get(f"/ui/portals/{pid}/card")).text
    other = await repo.create_portal("https://portal.example/x")
    assert (await client.post(f"/portals/{other}/break")).status_code == 400


async def test_break_applies_a_mutation_and_refreshes(client, repo, lab):
    from watchtower.lab.app import STATE

    pid = await repo.create_portal(lab.url("ssr"))
    r = await client.post(f"/portals/{pid}/break")
    assert r.status_code == 202 and r.json()["mutation"] and r.json()["runId"]
    assert STATE.mutations.get("ssr")


async def test_cost_endpoint(client, repo):
    pid = await repo.create_portal("https://portal.example/c")
    c = (await client.get(f"/portals/{pid}/cost")).json()
    assert {"jev_usd", "llm_usd", "total_usd", "projected_4500"} <= set(c)


async def test_race_runs_jev_and_llm_side_by_side(app, client, repo, lab, settings):
    app.state.judges = (FakeJev(), LLM(settings, backend=FakeLLMBackend()))
    pid = await repo.create_portal(lab.url("ssr"))
    r = await client.post(f"/portals/{pid}/race")
    assert r.status_code == 200, r.text
    out = r.json()
    for side in ("jev", "llm"):
        assert {"ms", "usd", "answer"} <= set(out[side])
    assert out["jev"]["answer"] == "tender_list" and out["llm"]["answer"] == "tender_list"
    assert out["llm"]["usd"] > 0


async def test_vision_emits_boxes_and_never_breaks(monkeypatch):
    from watchtower.dashboard.vision import look

    class J:
        async def locate(self, png, target, *, portal_id=None):
            return Box(0.1, 0.5, 0.0, 1.0, 0.8) if "table" in target else None

    seen = []
    monkeypatch.setattr(events, "emit", lambda pid, kind, data: seen.append((kind, data)))
    await look(J(), b"png", "p1", "https://x/")
    assert seen and seen[0][0] == "vision" and seen[0][1]["boxes"][0]["label"] == "tender list"

    class Broken:
        async def locate(self, *a, **k):
            raise RuntimeError("boom")

    seen.clear()
    await look(Broken(), b"png", "p1", "https://x/")
    assert seen == []
