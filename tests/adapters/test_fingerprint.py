import json
from pathlib import Path

import pytest

from watchtower.adapters import ADAPTERS, matching
from watchtower.brain.fingerprint import fingerprint
from watchtower.fetch import Page
from watchtower.recipe.engine import run_recipe

FX = Path(__file__).parent / "fixtures"

CASES = {
    "atexo": ("https://marches.maximilien.fr/?page=Entreprise.EntrepriseAdvancedSearch&AllCons", "atexo_list.html"),
    "netserver": ("https://vergabe.vmstart.de/NetServer/PublicationSearchControllerServlet?function=SearchPublications"
                  "&Gesetzesgrundlage=All&Category=InvitationToTender&thContext=publications", "netserver_list.html"),
    "tenderned": ("https://www.tenderned.nl/aankondigingen/overzicht", "tenderned_overview.html"),
    "evergabe": ("https://www.evergabe-online.de/search.html?234&cookieCheck", "evergabe_list.html"),
}


def page(url, name):
    return Page(url=url, final_url=url, status=200, html=(FX / name).read_text(), headers={})


def test_at_least_three_adapters():
    assert len(ADAPTERS) >= 4


@pytest.mark.parametrize("name", CASES)
def test_each_fixture_matches_exactly_one_adapter(name):
    url, f = CASES[name]
    assert [a.NAME for a in matching(page(url, f))] == [name]
    r = fingerprint(page(url, f))
    assert r is not None and r.platform == name


@pytest.mark.parametrize("url,f", [("https://vergabemarktplatz.brandenburg.de/VMPCenter/", "vmp_search.html"),
                                   ("https://example.org/", None)])
def test_generic_pages_match_none(url, f):
    html = (FX / f).read_text() if f else "<html><body><table><tr><td>Ausschreibung</td></tr></table></body></html>"
    p = Page(url=url, final_url=url, status=200, html=html, headers={})
    assert matching(p) == [] and fingerprint(p) is None


class FixtureFetcher:
    """Serves the saved list page/JSON for the list URL; everything else 404s (no network in tests)."""

    def __init__(self, routes: dict[str, str]):
        self.routes = routes

    def _find(self, url):
        return next((v for k, v in self.routes.items() if url.startswith(k)), None)

    async def get(self, url):
        f = self._find(url)
        return Page(url=url, final_url=url, status=200 if f else 404, html=(FX / f).read_text() if f else "",
                    headers={})

    async def get_json(self, url, params=None):
        if params and int(params.get("page", 0)) > 0:
            return {"content": []}
        f = self._find(url)
        if not f:
            raise IOError("404")
        return json.loads((FX / f).read_text())


async def _run(name, routes):
    url, f = CASES[name]
    r = fingerprint(page(url, f))
    r.pagination.max_pages = 1
    return await run_recipe(r, FixtureFetcher(routes), errors=[])


def test_atexo_reads_all_pages_by_postback():
    from watchtower.recipe.engine import postback_request

    url, f = CASES["atexo"]
    r = fingerprint(page(url, f))
    assert r.pagination.kind == "postback"
    action, data = postback_request((FX / f).read_text(), url, r.pagination.next_selector, r.pagination.param)
    assert action.startswith("https://marches.maximilien.fr/") and "PRADO_PAGESTATE" in data
    assert data["PRADO_POSTBACK_TARGET"] == "ctl0$CONTENU_PAGE$resultSearch$PagerTop$ctl2"


async def test_atexo_template_extracts_list():
    ts = await _run("atexo", {"https://marches.maximilien.fr/?page=Entreprise.EntrepriseAdvancedSearch": "atexo_list.html"})
    assert len(ts) == 10
    t = ts[0]
    assert t["id"].isdigit() and t["title"] and t["buyerName"] and "(" not in t["buyerName"]
    assert t["submissionDeadline"].tzinfo is not None and "/entreprise/consultation/" in t["url"]
    assert t.get("contractNature") is not None


async def test_netserver_template_extracts_list():
    ts = await _run("netserver", {"https://vergabe.vmstart.de/NetServer/PublicationSearchControllerServlet": "netserver_list.html"})
    assert len(ts) == 50
    t = ts[0]
    assert t["id"].startswith("54321-NetTender-") and t["title"] and t["buyerName"]
    assert t["referenceNumber"] == "RNK_EBBAU-2026-0061"
    assert str(t["submissionDeadline"]) == "2026-12-11 09:30:00+01:00"
    assert "TOID=54321-NetTender-" in t["url"]


async def test_tenderned_template_extracts_list():
    ts = await _run("tenderned", {"https://www.tenderned.nl/papi/tenderned-rs-tns/v2/publicaties": "tenderned_list.json"})
    assert len(ts) == 5
    t = ts[0]
    assert t["id"] == "450447" and t["title"] and t["buyerName"] == "Stichting Xpect013"
    assert t["procedureType"] == "restricted" and t["contractNature"] == "works"
    assert t["submissionDeadline"].utcoffset() is not None


async def test_tenderned_asks_only_for_open_contract_notices():
    from datetime import datetime, timezone, date

    seen = []

    class Spy(FixtureFetcher):
        async def get_json(self, url, params=None):
            seen.append(url)
            return await super().get_json(url, params)

    url, f = CASES["tenderned"]
    r = fingerprint(page(url, f))
    r.pagination.max_pages = 1
    await run_recipe(r, Spy({"https://www.tenderned.nl/papi/tenderned-rs-tns/v2/publicaties": "tenderned_list.json"}),
                     errors=[])
    assert "publicatieType=AAO" in seen[0] and f"sluitingsDatumVanaf={datetime.now(timezone.utc).date().isoformat()}"  # the engine uses UTC in seen[0]
    assert fingerprint(page(url, f)).pagination.max_pages >= 100


async def test_evergabe_template_extracts_list_and_detail():
    url, f = CASES["evergabe"]
    r = fingerprint(page(url, f))
    assert r.pagination.kind == "next_link" and r.list_url == "https://www.evergabe-online.de/search.html"
    r.pagination.max_pages = 1
    ts = await run_recipe(r, FixtureFetcher({"https://www.evergabe-online.de/search.html": "evergabe_list.html",
                                             "https://www.evergabe-online.de/tenderdetails.html?id=908814":
                                                 "evergabe_detail.html"}), errors=[])
    assert len(ts) == 10
    by_id = {t["id"]: t for t in ts}
    t = by_id["908814"]
    assert t["url"] == "https://www.evergabe-online.de/tenderdetails.html?id=908814"
    assert t["title"].startswith("Rahmenvereinbarung") and t["referenceNumber"] == "1366-26"
    assert t["buyerName"] == "Bundesamt für Strahlenschutz"  # from the detail page, without the region prefix
    assert str(t["submissionDeadline"]) == "2026-12-08 10:00:00+01:00"
    assert str(t["publicationDate"]) == "2026-11-20" and t["procedureType"] == "open"
    assert not t.get("documents")  # downloads need a Wicket page state the load balancer loses: left out, not guessed


async def test_evergabe_reads_one_page_and_strips_state_prefix_from_buyer():
    url, f = CASES["evergabe"]
    r = fingerprint(page(url, f))
    assert r.pagination.max_pages == 1  # next-page links are Wicket state links that answer 403 over plain HTTP
    detail = (FX / "evergabe_detail.html").read_text().replace("Bundesamt für Strahlenschutz",
                                                               "Sachsen-Anhalt: Stadt Genthin")

    class Prefixed(FixtureFetcher):
        async def get(self, u):
            if "tenderdetails" in u:
                return Page(url=u, final_url=u, status=200, html=detail, headers={})
            return await super().get(u)
    errors = []
    ts = await run_recipe(r, Prefixed({"https://www.evergabe-online.de/search.html": "evergabe_list.html"}),
                          errors=errors)
    assert {t["buyerName"] for t in ts} == {"Stadt Genthin"}
    assert any(e.startswith("list incomplete") for e in errors)  # so a read never closes unseen tenders
