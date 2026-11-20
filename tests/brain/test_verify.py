from datetime import date, datetime, timedelta

import httpx
import pytest

from tests.brain.fakes import FakeJev, NoJudge
from tests.helpers import SimpleFetcher
from tests.recipe.test_engine import ssr_recipe
from watchtower.brain.verify import cross_check, rule_check, select_value, verify_sample
from watchtower.recipe.engine import run_recipe, visible_text
from watchtower.recipe.normalize import parse_deadline, tz_for

PAGE = "Vergabenummer VgV-2026-1 Veröffentlicht am 01.12.2026 Angebotsfrist 15.12.2026 12:00 Auftraggeber Stadt"


async def _sample(lab, n=5):
    pages: dict[str, str] = {}
    tenders = await run_recipe(ssr_recipe(lab), SimpleFetcher(), limit=n, pages=pages)
    return tenders, pages


async def test_select_value_picks_candidate_verbatim():
    v = await select_value("submissionDeadline", PAGE, None, FakeJev(), "DE")
    assert v == parse_deadline("15.12.2026 12:00", "DE")


async def test_select_value_none_when_absent():
    assert await select_value("referenceNumber", "no numbers here", None, FakeJev()) is None


def test_rule_check_deadline_before_publication():
    t = {"id": "a", "url": "https://x/1", "publicationDate": date(2026, 12, 1),
         "submissionDeadline": datetime(2026, 11, 1, 12, tzinfo=tz_for("DE"))}
    assert rule_check(t) == ["submissionDeadline before publicationDate"]


def test_rule_check_relative_url():
    assert "url not absolute" in rule_check({"id": "a", "url": "/t/1"})


async def test_correct_sample_ok(lab):
    tenders, pages = await _sample(lab)
    rep = await verify_sample(tenders, pages, FakeJev())
    assert rep.ok and rep.field_scores["submissionDeadline"] > 0.8 and not rep.nulled


async def test_wrong_deadline_nulled(lab):
    tenders, pages = await _sample(lab)
    for t in tenders:
        t["submissionDeadline"] = t["submissionDeadline"] + timedelta(days=3)
    rep = await verify_sample(tenders, pages, FakeJev())
    assert "submissionDeadline" in rep.nulled and not rep.ok
    assert any("submissionDeadline" in f and "should" in f for f in rep.feedback)


async def test_wrong_optional_field_masked_but_ok(lab):
    tenders, pages = await _sample(lab)
    for t in tenders:
        t["referenceNumber"] = "WRONG-123"
    rep = await verify_sample(tenders, pages, FakeJev())
    assert rep.ok and "referenceNumber" in rep.mask


async def test_duplicate_ids_not_ok(lab):
    tenders, pages = await _sample(lab)
    tenders[1]["id"] = tenders[0]["id"]
    rep = await verify_sample(tenders, pages, FakeJev())
    assert not rep.ok and any("duplicate" in p for p in rep.problems)


async def test_empty_not_ok():
    rep = await verify_sample([], {}, FakeJev())
    assert not rep.ok


async def test_no_judge_falls_back_to_rules(lab):
    tenders, pages = await _sample(lab)
    rep = await verify_sample(tenders, pages, NoJudge())
    assert rep.ok and rep.judged is False
