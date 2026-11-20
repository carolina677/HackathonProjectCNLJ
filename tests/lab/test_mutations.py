import httpx
import pytest

from watchtower.lab.mutations import MUTATIONS
from watchtower.lab.scorer import score
from watchtower.models import TenderStatus, tender_json

HTML_MUTATIONS = ["rename_classes", "reshuffle_layout", "pagination_style", "date_format"]


@pytest.mark.parametrize("name", HTML_MUTATIONS)
async def test_html_mutation_changes_markup_not_truth(lab, name):
    async with httpx.AsyncClient() as c:
        before = (await c.get(lab.url("ssr"))).text
        ids_before = [t.id for t in lab.truth("ssr")]
        lab.mutate("ssr", name)
        after = (await c.get(lab.url("ssr"))).text
    assert before != after and [t.id for t in lab.truth("ssr")] == ids_before


@pytest.mark.parametrize("name", ["api_path_move", "json_keys_rename"])
async def test_api_mutations(lab, name):
    async with httpx.AsyncClient() as c:
        old = await c.get(lab.url("spa") + "api/tenders")
        lab.mutate("spa", name)
        new = await c.get(lab.url("spa") + "api/tenders")
    assert old.status_code == 200
    assert new.status_code == 404 if name == "api_path_move" else "bezeichnung" in new.text


async def test_withdraw_one_updates_truth(lab):
    lab.mutate("ssr", "withdraw_one")
    withdrawn = [t for t in lab.truth("ssr") if t.status == TenderStatus.withdrawn]
    assert len(withdrawn) == 1
    async with httpx.AsyncClient() as c:
        html = (await c.get(withdrawn[0].url)).text
    assert "Aufgehoben" in html


async def test_deadline_passed_updates_truth(lab):
    lab.mutate("ssr", "deadline_passed")
    assert sum(t.status == TenderStatus.closed for t in lab.truth("ssr")) == 1


async def test_site_down_and_login(lab):
    async with httpx.AsyncClient() as c:
        lab.mutate("ssr", "site_down")
        assert (await c.get(lab.url("ssr"))).status_code == 503
        lab.reset("ssr")
        lab.mutate("ssr", "add_login")
        assert 'type="password"' in (await c.get(lab.url("ssr"))).text


async def test_break_endpoint_picks_repairable(lab):
    async with httpx.AsyncClient() as c:
        r = (await c.post(lab.base + "/lab/ssr/break")).json()
    assert r["mutation"] in MUTATIONS


def _api(truth):
    return [tender_json(t) for t in truth]


def test_score_perfect(lab):
    t = lab.truth("ssr")
    s = score(_api(t), t)
    assert s.attach == 1.0 and s.quality == 1.0 and s.wrong_values == 0


def test_wrong_worse_than_missing(lab):
    t = lab.truth("ssr")
    wrong, missing = _api(t), _api(t)
    wrong[0]["submissionDeadline"] = "2030-01-01T12:00:00+01:00"
    missing[0]["submissionDeadline"] = None
    sw, sm = score(wrong, t), score(missing, t)
    assert sw.wrong_values == 1 and sm.missing_values == 1
    assert sw.quality < sm.quality < 1.0


def test_documents_compared_by_hash(lab):
    t = lab.truth("ssr")
    api = _api(t)
    api[0]["documents"] = []
    assert score(api, t).field_accuracy["documents"] < 1.0


def test_attach_counts_missing_tenders(lab):
    t = lab.truth("ssr")
    assert score(_api(t)[:10], t).attach == pytest.approx(10 / 23)
