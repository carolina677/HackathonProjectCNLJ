"""Unified Briefly UI on GET /: all workspace, tender, portal health, repair compare, and live controls."""

from pathlib import Path

from watchtower import repairs


async def test_unified_briefly_is_served_at_root(client):
    r = await client.get("/")
    assert r.status_code == 200
    for needle in (
        "Watchtower",
        'id="overview"',
        'id="portals-section"',
        'id="tender-section"',
        'id="repair-view"',
        'id="frame-before"',
        'id="frame-after"',
        "sandbox",
        'id="activity-chart"',
        'id="brief-btn"',
        'id="add-form"',
        'id="detail"',
    ):
        assert needle in r.text

    js = (await client.get("/static/app.js")).text
    for endpoint in ("/portals", "/repairs", "/ui/tenders", "before.html"):
        assert endpoint in js

    assert (await client.get("/static/app.css")).status_code == 200
    redir = await client.get("/briefly", follow_redirects=False)
    assert redir.status_code in (302, 307) and redir.headers["location"] == "/"


async def test_portal_card_shows_before_vs_after_link_when_repaired(client, repo, settings):
    data_dir = Path(settings.data_dir)
    pid = await repo.create_portal("https://repaired.example/list")
    snap_dir = data_dir / "snapshots" / pid
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "1.html").write_text("<html><body><tr class='old'><a href='/1'>A</a></tr></body></html>")
    (snap_dir / "2.html").write_text("<html><body><article class='new'><a href='/1'>A</a></article></body></html>")
    repairs.record(
        data_dir, pid, ok=True, reason="Layout changed", diff=["item: tr.old → article.new"], attempts=1,
        before_ref=f"snapshots/{pid}/1.html", before_url="https://repaired.example/list",
        before_recipe={"source": "html", "item": "tr.old"},
        after_ref=f"snapshots/{pid}/2.html", after_url="https://repaired.example/list",
        after_recipe={"source": "html", "item": "article.new"},
    )
    card = await client.get(f"/ui/portals/{pid}/card")
    assert card.status_code == 200
    assert f"/#repair/{pid}/1" in card.text and "Before vs after" in card.text
