import json

import pytest

from tests.brain.fakes import FakeJev
from tests.helpers import SimpleFetcher
from tests.recipe.test_engine import ssr_recipe
from watchtower.brain.explore import explore
from watchtower.brain.induce import build_prompt, country_from_url, induce_recipe, trim_html
from watchtower.fetch import Fetcher, close_browser
from watchtower.judge.llm import LLM
from watchtower.recipe.engine import run_recipe
from watchtower.scheduler import HostLimiter


class RecordingBackend:
    """Replays a recorded LLM answer and keeps the prompt it was given."""

    def __init__(self, answer: dict):
        self.answer, self.prompts = answer, []

    async def __call__(self, system, user, schema):
        self.prompts.append((system, user))
        return self.answer, 30000, 1500


@pytest.fixture
async def ssr_exploration(lab, tmp_path):
    f = Fetcher("p", HostLimiter(min_interval=0.0), snapshot_dir=tmp_path)
    ex = await explore(lab.url("ssr"), f, FakeJev(), portal_id="p")
    yield ex
    await close_browser()


def test_trim_html_collapses_rows():
    rows = "".join(f"<tr class='r'><td>{i}</td></tr>" for i in range(30))
    out = trim_html(f"<html><head><script>x()</script></head><body><table>{rows}</table></body></html>")
    assert out.count("<tr") == 3 and "27 more similar" in out and "x()" not in out


def test_country_from_url():
    assert country_from_url("https://www.evergabe.de/x") == "DE"
    assert country_from_url("https://www.boamp.fr/") == "FR"
    assert country_from_url("http://127.0.0.1:8000/lab") is None


async def test_prompt_contains_evidence(ssr_exploration):
    p = build_prompt(ssr_exploration, feedback=["submissionDeadline wrong in 3/5 samples"])
    assert "tender-row" in p and "Angebotsfrist" in p
    assert "submissionDeadline wrong" in p
    assert "Recipe" in p or "list_url" in p


async def test_induced_recipe_runs_on_lab_ssr(lab, ssr_exploration, settings):
    recorded = json.loads(ssr_recipe(lab).model_dump_json())
    backend = RecordingBackend(recorded)
    recipe = await induce_recipe(ssr_exploration, LLM(settings, backend=backend), portal_id="p")
    tenders = await run_recipe(recipe, SimpleFetcher())
    assert len(tenders) >= 20 and all(t["id"] and t["title"] for t in tenders)
    assert recipe.country == "DE"


@pytest.mark.live
async def test_live_llm_induces_ssr(lab, ssr_exploration):
    from watchtower.config import Settings

    recipe = await induce_recipe(ssr_exploration, LLM(Settings()), portal_id="p")
    tenders = await run_recipe(recipe, SimpleFetcher())
    assert len(tenders) == 23
