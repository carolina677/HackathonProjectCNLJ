"""Before vs after view of a repair: highlighting, safety, history and endpoints."""

from pathlib import Path

from watchtower import repairs

OLD = """<html><head><title>Liste</title><script>track()</script></head><body>
<h1>Ausschreibungen</h1>
<table class="tender-table"><tr class="tender-row"><td class="nr">A1</td><td><a href="/t/1">Bau</a></td></tr>
<tr class="tender-row"><td class="nr">A2</td><td><a href="/t/2">IT</a></td></tr></table>
<p class="footer">Impressum</p></body></html>"""

NEW = """<html><head><title>Liste</title></head><body>
<h1>Ausschreibungen</h1>
<section class="results"><article class="card" onclick="steal()"><h3><a href="javascript:alert(1)">Bau</a></h3></article>
<article class="card"><h3><a href="/t/2">IT</a></h3></article></section>
<iframe src="https://evil.example"></iframe>
<p class="footer">Impressum</p></body></html>"""


def test_after_marks_new_structure_and_recipe_items():
    out, stats = repairs.render(NEW, other_html=OLD, item_selector="article.card", side="after", base_url="https://p.example/list")
    assert stats["items"] == 2
    assert 'class="results wt-new"' in out          # the new container, marked once (outermost only)
    assert out.count("wt-new") == 1 + out.count(".wt-new")  # children of a marked element are not marked again
    assert out.count("wt-item") >= 2
    assert '<base href="https://p.example/list">' in out and 'id="wt-style"' in out
    assert "footer wt-" not in out                   # unchanged structure is not highlighted


def test_before_marks_what_disappeared():
    out, stats = repairs.render(OLD, other_html=NEW, item_selector="tr.tender-row", side="before", base_url=None)
    assert 'class="tender-table wt-gone"' in out and stats["items"] == 2


def test_rendered_page_is_inert():
    out, _ = repairs.render(NEW, other_html=OLD, item_selector=None, side="after", base_url=None)
    low = out.lower()
    assert "<script" not in low and "<iframe" not in low and "onclick" not in low and "javascript:" not in low
    old, _ = repairs.render(OLD, other_html=NEW, item_selector=None, side="before", base_url=None)
    assert "track()" not in old


def test_bad_or_json_selector_never_crashes():
    out, stats = repairs.render(NEW, other_html=OLD, item_selector="[[[", side="after", base_url=None)
    assert stats["items"] == 0 and "<html" in out.lower()
    assert repairs._html_items("$.items[*]", "json_api") is None
    assert repairs._html_items("//tr", "html") is None


def _snap(data_dir: Path, pid: str, name: str, html: str) -> str:
    d = data_dir / "snapshots" / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(html)
    return f"snapshots/{pid}/{name}"


def _record(data_dir: Path, pid: str, ok: bool = True):
    return repairs.record(
        data_dir, pid, ok=ok, reason="The portal listed no tenders.", diff=["item: tr.tender-row → article.card"], attempts=1,
        before_ref=_snap(data_dir, pid, "1.html", OLD), before_url="https://p.example/list",
        before_recipe={"source": "html", "item": "tr.tender-row"},
        after_ref=_snap(data_dir, pid, "2.html", NEW), after_url="https://p.example/list",
        after_recipe={"source": "html", "item": "article.card"} if ok else None)


def test_record_and_list(tmp_path):
    r1 = _record(tmp_path, "p1")
    r2 = _record(tmp_path, "p1", ok=False)
    assert r1["n"] == 1 and r2["n"] == 2
    assert r1["before"]["items"] == 2 and r1["after"]["items"] == 2 and r1["after"]["changed"] >= 1
    assert [r["n"] for r in repairs.list_repairs(tmp_path, "p1")] == [2, 1]   # newest first
    assert repairs.page(tmp_path, "p1", 1, "after") and repairs.page(tmp_path, "p1", 1, "sideways") is None
    assert repairs.page(tmp_path, "p1", 99, "before") is None


def test_history_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(repairs, "KEEP", 3)
    for _ in range(5):
        _record(tmp_path, "p1")
    assert [r["n"] for r in repairs.list_repairs(tmp_path, "p1")] == [5, 4, 3]


def test_snapshot_paths_cannot_escape_data_dir(tmp_path):
    (tmp_path / "secret.txt").write_text("nope")
    assert repairs._read_html(tmp_path, "../secret.txt") is None
    assert repairs._read_html(tmp_path, "snapshots/../secret.txt") is None


async def test_endpoints(client, repo, settings):
    pid = await repo.create_portal("https://p.example/list")
    assert (await client.get(f"/portals/{pid}/repairs")).json() == []
    _record(Path(settings.data_dir), pid)
    lst = (await client.get(f"/portals/{pid}/repairs")).json()
    assert len(lst) == 1 and lst[0]["ok"] and lst[0]["diff"]
    r = await client.get(f"/portals/{pid}/repairs/1/after.html")
    assert r.status_code == 200 and "wt-item" in r.text
    assert "sandbox" in r.headers["content-security-policy"]
    assert (await client.get(f"/portals/{pid}/repairs/1/before.html")).status_code == 200
    assert (await client.get(f"/portals/{pid}/repairs/9/after.html")).status_code == 404
    assert (await client.get(f"/portals/{pid}/repairs/1/middle.html")).status_code == 404
    assert (await client.get("/portals/nope/repairs")).status_code == 404
    page = (await client.get(f"/portals/{pid}/theater")).text
    assert 'id="compare"' in page and 'sandbox' in page
