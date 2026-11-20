"""Briefly preview: served as static files, wired to the live portals and the before/after view."""


async def test_briefly_preview_is_served_with_repair_view(client):
    r = await client.get("/static/briefly-preview.html")
    assert r.status_code == 200
    for needle in ('id="repair-view"', 'id="frame-before"', 'id="frame-after"', "sandbox", 'id="portal-list"'):
        assert needle in r.text
    js = (await client.get("/static/briefly-preview.js")).text
    assert "/portals" in js and "/repairs" in js and "before.html" in js
    assert (await client.get("/static/briefly-preview.css")).status_code == 200
    redir = await client.get("/briefly", follow_redirects=False)
    assert redir.status_code in (302, 307) and redir.headers["location"].endswith("/static/briefly-preview.html")
