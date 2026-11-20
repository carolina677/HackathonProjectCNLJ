import json

import pytest

from tests.brain.fakes import FakeJev
from tests.brain.test_induce import RecordingBackend
from tests.helpers import SimpleFetcher
from tests.recipe.test_engine import ssr_recipe
from watchtower.brain.heal import heal
from watchtower.fetch import Fetcher, close_browser
from watchtower.judge.llm import LLM
from watchtower.recipe.engine import run_recipe
from watchtower.scheduler import HostLimiter


def renamed(lab):
    r = json.loads(ssr_recipe(lab).model_dump_json())
    r["item"] = "tr.vx-wor-rednet"
    r["fields"]["id"]["selector"] = "td.vx-rn"
    r["fields"]["buyerName"]["selector"] = "td.vx-reyub"
    r["fields"]["publicationDate"]["selector"] = "td.vx-dehsilbup"
    r["documents"] = [{"selector": "ul.vx-stnemucod a", "attr": "href"}]
    return r


@pytest.fixture
async def fetcher(tmp_path):
    yield Fetcher("p", HostLimiter(min_interval=0.0), snapshot_dir=tmp_path)
    await close_browser()


async def test_heal_after_class_rename(lab, fetcher, settings):
    old = ssr_recipe(lab)
    lab.mutate("ssr", "rename_classes")
    backend = RecordingBackend(renamed(lab))
    new, rep = await heal("p", lab.url("ssr"), old, "<table><tr class='tender-row'>…</tr></table>", fetcher,
                          FakeJev(), LLM(settings, backend=backend), reason="list selector matched no items")
    assert new is not None and rep.ok
    ids = [t["id"] for t in await run_recipe(new, SimpleFetcher())]
    assert ids == [t.id for t in lab.truth("ssr")]
    system, user = backend.prompts[0]
    assert "tr.tender-row" in user and "list selector matched no items" in user


async def test_heal_gives_up_after_attempts(lab, fetcher, settings):
    old = ssr_recipe(lab)
    lab.mutate("ssr", "rename_classes")
    backend = RecordingBackend(json.loads(old.model_dump_json()))  # keeps returning the broken recipe
    new, rep = await heal("p", lab.url("ssr"), old, None, fetcher, FakeJev(), LLM(settings, backend=backend),
                          reason="selectors broke", attempts=2)
    assert new is None and len(backend.prompts) == 2


async def test_heal_reports_block(lab, fetcher, settings):
    lab.mutate("ssr", "add_login")
    new, rep = await heal("p", lab.url("ssr"), ssr_recipe(lab), None, fetcher, FakeJev(),
                          LLM(settings, backend=RecordingBackend({})), reason="x")
    assert new is None and rep.blocked == "login"


def test_recipe_diff_names_leaf_changes(lab):
    from watchtower.brain.heal import recipe_diff
    from watchtower.recipe.dsl import Recipe

    old = ssr_recipe(lab)
    new = Recipe.model_validate(renamed(lab))
    d = recipe_diff(old, new)
    assert "item: tr.tender-row → tr.vx-wor-rednet" in d
    assert "fields.id.selector: td.nr → td.vx-rn" in d
    assert all("{" not in line for line in d)
