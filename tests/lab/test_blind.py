"""Blind lab: a portal Watchtower has never seen, generated fresh from a seed, with exact ground truth."""

import hashlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from watchtower.lab.blind import blind_spec, create_blind_app


@pytest.fixture
def blind():
    host = FastAPI()
    host.mount("/blind", create_blind_app())
    return TestClient(host)


def test_same_seed_gives_the_same_portal(blind):
    assert blind_spec(4711) == blind_spec(4711)
    assert blind.get("/blind/4711/").text == blind.get("/blind/4711/").text


def test_seeds_give_different_portals():
    specs = [blind_spec(s) for s in range(40)]
    assert len({s.layout for s in specs}) == 3
    assert len({s.source for s in specs}) == 2
    assert len({s.date_fmt for s in specs}) >= 3
    assert len({s.lang for s in specs}) >= 3
    assert len({s.paging for s in specs}) == 3
    assert len({s.detail_seg for s in specs}) >= 3


def test_truth_has_every_tender_with_its_blind_url(blind):
    spec = blind_spec(7)
    t = blind.get("/blind/7/truth").json()
    assert len(t) == 23 and len({x["id"] for x in t}) == 23
    assert all(x["url"] == f"http://testserver/blind/7/{spec.detail_seg}/{x['id']}" for x in t)
    assert not any(x["id"].startswith("LAB-") for x in t)


def _html_seed(want_paging: str) -> int:
    return next(s for s in range(500) if blind_spec(s).source == "html" and blind_spec(s).paging == want_paging)


@pytest.mark.parametrize("paging", ["page", "offset", "next"])
def test_html_listing_pages_cover_every_tender(blind, paging):
    seed = _html_seed(paging)
    spec = blind_spec(seed)
    ids = {x["id"] for x in blind.get(f"/blind/{seed}/truth").json()}
    seen, url, hops = set(), f"/blind/{seed}/", 0
    while url and hops < 10:
        page = blind.get(url).text
        seen |= set(re.findall(rf"/{spec.detail_seg}/([^'\"/?]+)['\"]", page))
        nxt = re.search(r"rel='next' href='([^']+)'", page)
        url, hops = (nxt.group(1) if nxt else None), hops + 1
    assert seen == ids


def test_detail_page_shows_the_true_values(blind):
    seed = _html_seed("page")
    t = blind.get(f"/blind/{seed}/truth").json()[0]
    page = blind.get(t["url"].replace("http://testserver", "")).text
    for v in (t["title"], t["buyerName"], t["referenceNumber"]):
        assert v in page


def test_documents_match_truth_hashes(blind):
    seed = _html_seed("page")
    spec = blind_spec(seed)
    t = blind.get(f"/blind/{seed}/truth").json()[0]
    r = blind.get(f"/blind/{seed}/{spec.file_seg}/{t['id']}/1")
    assert r.headers["content-type"] == "application/pdf"
    assert t["documents"][0]["fileName"] in r.headers["content-disposition"]
    assert hashlib.sha256(r.content).hexdigest() == t["documents"][0]["sha256"]


def test_json_source_serves_a_shell_and_an_api_with_its_own_keys(blind):
    seed = next(s for s in range(500) if blind_spec(s).source == "json")
    spec = blind_spec(seed)
    shell = blind.get(f"/blind/{seed}/").text
    assert spec.api_path in shell and "<script>" in shell
    j = blind.get(f"/blind/{seed}/{spec.api_path}", params={spec.json_offset: 0}).json()
    item = j[spec.json_keys["items"]][0]
    assert spec.json_keys["title"] in item and "title" != spec.json_keys["title"]


def test_bad_seed_is_not_found(blind):
    assert blind.get("/blind/abc/").status_code == 404


def test_blind_lab_is_mounted_only_with_the_lab(settings, tmp_path):
    from watchtower.app import create_app
    from watchtower.config import Settings

    with TestClient(create_app(settings, start_scheduler=False)) as c:
        assert c.get("/blind/3/").status_code == 200
    off = Settings(data_dir=tmp_path / "off", lab=False, typesafe_api_key=None, anthropic_api_key=None,
                   openai_api_key=None)
    with TestClient(create_app(off, start_scheduler=False)) as c:
        assert c.get("/blind/3/").status_code == 404
