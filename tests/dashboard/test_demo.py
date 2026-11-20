import asyncio
import httpx

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
        assert res["status"] == "started" and len(res["portals"]) == 4

        tasks = list(app.state.demo_tasks)
        assert tasks
        await asyncio.gather(*tasks)

        portals = {p["url"]: p for p in (await client.get("/portals")).json()}
        assert portals["http://test/lab/ssr"]["state"] == "serving"
        assert portals["http://test/lab/multistep"]["state"] == "serving"
        assert portals["http://test/lab/captcha-open-docs"]["state"] == "serving"
        assert portals["http://test/lab/login"]["state"] == "failed"
        assert "login" in portals["http://test/lab/login"]["reason"].lower()
        assert STATE.has("ssr", "rename_classes")

        tenders = (await client.get(f"/portals/{ssr_id}/tenders")).json()
        assert len(tenders) == 23 and tenders[0]["documents"]
        doc_path = tenders[0]["documents"][0]["path"]
        pdf = await client.get(doc_path)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

        reps = (await client.get(f"/portals/{ssr_id}/repairs")).json()
        assert len(reps) == 1 and reps[0]["ok"]
        before = (await client.get(f"/portals/{ssr_id}/repairs/1/before.html")).text
        after = (await client.get(f"/portals/{ssr_id}/repairs/1/after.html")).text
        assert "wt-gone" in before and "wt-new" in after and after.count("wt-item") >= 10
    finally:
        STATE.reset()


async def test_timelapse_endpoint_returns_202_when_lab_enabled(client):
    STATE.reset()
    try:
        r = await client.post("/demo/timelapse")
        assert r.status_code == 202 and r.json()["status"] == "started"
    finally:
        STATE.reset()
