import pytest

from watchtower import events
from watchtower.fetch import Fetcher, PortalCancelled, close_browser, needs_js


class CountingLimiter:
    def __init__(self):
        self.calls: list[str] = []

    async def acquire(self, host: str):
        self.calls.append(host)


@pytest.fixture
async def fetcher(tmp_path):
    lim = CountingLimiter()
    f = Fetcher("p1", lim, cancelled=lambda: False, snapshot_dir=tmp_path)
    yield f, lim
    await close_browser()


async def test_get_and_limiter_used(lab, fetcher):
    f, lim = fetcher
    page = await f.get(lab.url("ssr"))
    assert page.status == 200 and "tender-row" in page.html
    assert lim.calls == ["127.0.0.1"]


async def test_needs_js(lab, fetcher):
    f, _ = fetcher
    assert needs_js(await f.get(lab.url("spa")))
    assert not needs_js(await f.get(lab.url("ssr")))


async def test_render_captures_json_api(lab, fetcher):
    f, lim = fetcher
    rp = await f.render(lab.url("spa"))
    assert any("api/tenders" in c.url and isinstance(c.body, dict) and c.body.get("items") for c in rp.json_responses)
    assert "Ausschreibungen" in rp.html
    assert rp.screenshot and rp.screenshot[:2] == b"\xff\xd8"
    assert any("/lab/spa/tender/LAB-0001" in l.href for l in rp.links)
    assert len(lim.calls) >= 2  # page + xhr both went through the limiter


async def test_cancelled_raises(lab, tmp_path):
    f = Fetcher("p1", CountingLimiter(), cancelled=lambda: True, snapshot_dir=tmp_path)
    with pytest.raises(PortalCancelled):
        await f.get(lab.url("ssr"))


async def test_stream(lab, fetcher):
    f, _ = fetcher
    t = lab.truth("ssr")[0]
    async with f.stream(lab.doc_url("ssr", t.id, 1)) as r:
        body = b"".join([c async for c in r.aiter_bytes()])
    assert body.startswith(b"%PDF") and "filename" in r.headers.get("content-disposition", "")


async def test_snapshot_written(lab, fetcher, tmp_path):
    f, _ = fetcher
    ref = f.snapshot(await f.get(lab.url("ssr")))
    assert (tmp_path / ref).read_text().count("tender-row") == 10


async def test_page_event(lab, fetcher):
    f, _ = fetcher
    await f.get(lab.url("ssr"))
    ev = events.history("p1", {"page"})[-1]["data"]
    assert ev["status"] == 200 and ev["url"].endswith("/lab/ssr/")


async def test_each_redirect_hop_is_throttled(lab, fetcher):
    f, lim = fetcher
    page = await f.get(lab.url("ssr").rstrip("/"))  # the lab answers /lab/ssr with a redirect to /lab/ssr/
    assert page.status == 200 and page.final_url.endswith("/lab/ssr/")
    assert len(lim.calls) == 2


def test_browser_throttles_every_request_it_lets_through():
    from watchtower.fetch import route_policy

    for rt in ("document", "xhr", "fetch", "script", "other", "websocket", "eventsource", "manifest", "ping"):
        assert route_policy(rt) == "throttle", rt
    for rt in ("image", "font", "media", "stylesheet"):
        assert route_policy(rt) == "abort", rt
