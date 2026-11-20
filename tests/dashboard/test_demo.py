import asyncio
import httpx

from watchtower import events
from watchtower.app import create_app
from watchtower.demo import run_timelapse
from watchtower.lab.app import STATE


async def test_timelapse_404_when_lab_disabled(settings):
    off = settings.model_copy(update={"lab": False})
    app = create_app(off, start_scheduler=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/demo/timelapse")
            assert r.status_code == 404
            assert "timelapse-btn" not in (await c.get("/")).text


async def test_timelapse_202_and_full_cycle(app, client):
    STATE.reset()
    try:
        assert "timelapse-btn" in (await client.get("/")).text
        res = await run_timelapse(app, "http://test", speed=200.0)
        ssr_id = res["ssrPortalId"]
        assert res["status"] == "started" and len(res["portals"]) == 5

        tasks = list(app.state.demo_tasks)
        assert tasks
        await asyncio.gather(*tasks)

        portals = {p["url"]: p for p in (await client.get("/portals")).json()}
        for slug in ("ssr", "multistep", "spa"):  # three portals broke at once and all repaired themselves
            assert portals[f"http://test/lab/{slug}"]["state"] == "serving", slug
            pid = portals[f"http://test/lab/{slug}"]["id"]
            reps = (await client.get(f"/portals/{pid}/repairs")).json()
            assert len(reps) == 1 and reps[0]["ok"] and reps[0]["diff"], slug
        docs = portals["http://test/lab/captcha-open-docs"]
        assert docs["state"] == "failed" and "login" in docs["reason"].lower()
        assert portals["http://test/lab/login"]["state"] == "failed"
        assert STATE.has("ssr", "rename_classes") and STATE.has("spa", "json_keys_rename")

        tenders = (await client.get(f"/portals/{ssr_id}/tenders")).json()
        assert len(tenders) == 23 and tenders[0]["documents"]
        pdf = await client.get(tenders[0]["documents"][0]["path"])
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

        before = (await client.get(f"/portals/{ssr_id}/repairs/1/before.html")).text
        after = (await client.get(f"/portals/{ssr_id}/repairs/1/after.html")).text
        assert "wt-gone" in before and "wt-new" in after and after.count("wt-item") >= 10

        story = [e["data"] for e in events.history(kinds={"story"})]
        assert story[0]["step"] == "start" and story[-1]["step"] == "done"
        assert all(s["mode"] == "replay" for s in story)
        final = story[-1]
        assert final["repairs"] == 3 and final["needs_human"] == 2 and final["tenders_lost"] == 0
        assert final["kept_served"] > 0 and final["usd"] > 0
    finally:
        STATE.reset()


async def test_chaos_breaks_serving_lab_portals_without_refresh(app, client):
    STATE.reset()
    try:
        await run_timelapse(app, "http://test", speed=200.0)
        await asyncio.gather(*list(app.state.demo_tasks))
        STATE.reset()  # the lab portals look like their recipes again
        r = await client.post("/demo/chaos")
        assert r.status_code == 202
        broken = r.json()["broken"]
        assert {b["slug"] for b in broken} == {"ssr", "multistep", "spa"}
        for b in broken:
            assert STATE.has(b["slug"], b["mutation"])
        runs_started = [e for e in events.history(kinds={"run"}) if e["data"].get("status") == "started"
                        and e["portal_id"] in {b["id"] for b in broken}]
        assert not runs_started or all(e["ts"] < broken[0]["at"] for e in runs_started)  # no refresh: the pulse finds it
        assert any(e["data"]["step"] == "chaos" and e["data"]["mode"] == "live" for e in events.history(kinds={"story"}))
    finally:
        STATE.reset()


async def test_chaos_404_when_lab_disabled(settings):
    off = settings.model_copy(update={"lab": False})
    app = create_app(off, start_scheduler=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post("/demo/chaos")).status_code == 404


async def test_timelapse_endpoint_returns_202_when_lab_enabled(client):
    STATE.reset()
    try:
        r = await client.post("/demo/timelapse")
        assert r.status_code == 202 and r.json()["status"] == "started"
    finally:
        STATE.reset()


def test_chaos_only_picks_breaks_the_pulse_can_see():
    from watchtower.demo import chaos_mutation

    STATE.reset()
    try:
        for _ in range(30):
            assert chaos_mutation("ssr") != "pagination_style"
            STATE.reset()
        assert chaos_mutation("spa") in ("json_keys_rename", "api_path_move")
    finally:
        STATE.reset()


async def test_replayed_portals_stay_watched_by_the_pulse(app):
    """The replay deletes and recreates lab portals under the same ids; the scheduler must not treat them as deleted."""
    from watchtower.scheduler import Scheduler

    sched = Scheduler(app.state.repo, object(), app.state.settings)
    app.state.scheduler = sched
    STATE.reset()
    try:
        for _ in range(2):  # the second run deletes the first run's portals
            res = await run_timelapse(app, "http://test", speed=200.0)
            await asyncio.gather(*list(app.state.demo_tasks))
        assert all(not sched.is_cancelled(p["id"]) for p in res["portals"])
    finally:
        STATE.reset()
        await sched.stop()
