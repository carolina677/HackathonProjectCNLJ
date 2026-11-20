import sys
import types
from contextlib import contextmanager

import pytest
from pydantic import BaseModel

from watchtower import tracing
from watchtower.judge.llm import LLM


class FakeSpan:
    def __init__(self, name, attributes):
        self.name, self.attributes, self.outputs = name, dict(attributes or {}), None

    def set_attributes(self, a):
        self.attributes.update(a)

    def set_outputs(self, o):
        self.outputs = o


@pytest.fixture
def fake_mlflow(monkeypatch):
    calls = {"uri": None, "experiment": None, "autolog": [], "spans": []}

    @contextmanager
    def start_span(name, attributes=None, **kw):
        s = FakeSpan(name, attributes)
        calls["spans"].append(s)
        yield s

    m = types.ModuleType("mlflow")
    m.set_tracking_uri = lambda u: calls.__setitem__("uri", u)
    m.set_experiment = lambda n: calls.__setitem__("experiment", n)
    m.start_span = start_span
    for prov in ("openai", "anthropic"):
        sub = types.ModuleType(f"mlflow.{prov}")
        sub.autolog = (lambda p: (lambda **kw: calls["autolog"].append(p)))(prov)
        setattr(m, prov, sub)
        monkeypatch.setitem(sys.modules, f"mlflow.{prov}", sub)
    monkeypatch.setitem(sys.modules, "mlflow", m)
    yield calls
    tracing.reset()


def test_tracing_off_without_tracking_uri(monkeypatch, fake_mlflow):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    assert tracing.setup() is False
    with tracing.span("x", {"a": 1}) as s:
        assert s is None
    assert fake_mlflow["spans"] == [] and fake_mlflow["autolog"] == []


def test_tracing_on_with_tracking_uri(monkeypatch, fake_mlflow):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
    assert tracing.setup() is True
    assert fake_mlflow["uri"] == "http://127.0.0.1:5000"
    assert fake_mlflow["experiment"] == "watchtower"
    assert sorted(fake_mlflow["autolog"]) == ["anthropic", "openai"]


def test_tracing_off_when_mlflow_missing(monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    monkeypatch.setitem(sys.modules, "mlflow", None)  # import mlflow raises ImportError
    try:
        assert tracing.setup() is False
    finally:
        tracing.reset()


class Answer(BaseModel):
    ok: bool


async def test_llm_call_gets_a_span_with_portal_and_cost(monkeypatch, fake_mlflow, settings):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    tracing.setup()

    async def backend(system, user, schema):
        return {"ok": True}, 1000, 100

    out = await LLM(settings, backend=backend).complete_json("s", "u", Answer, portal_id="p1", purpose="induce")
    assert out.ok
    [s] = fake_mlflow["spans"]
    assert s.name == "llm.induce"
    assert s.attributes["portal_id"] == "p1" and s.attributes["model"] == settings.llm_model
    assert s.outputs["tokens_in"] == 1000 and s.outputs["usd"] > 0


async def test_jev_call_gets_a_span(monkeypatch, fake_mlflow, settings):
    from tests.judge.test_jev import FakeJevClient, PAGE_TYPES, choice_answer
    from watchtower.judge.jev import Jev

    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    tracing.setup()
    client = FakeJevClient({"q": choice_answer({"tender_list": 0.9, "tender_detail": 0.05, "other": 0.05})},
                           tokens=500)
    await Jev(settings, client=client).choice({"html": "<table>"}, "Which page?", PAGE_TYPES, portal_id="p2")
    [s] = fake_mlflow["spans"]
    assert s.name == "jev" and s.attributes["portal_id"] == "p2"
    assert s.outputs["tokens_in"] == 500 and "answers" in s.outputs


def test_tracking_uri_from_env_file_settings(monkeypatch, fake_mlflow, tmp_path):
    from watchtower.config import Settings

    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    (tmp_path / ".env").write_text("MLFLOW_TRACKING_URI=http://127.0.0.1:5001\n")
    assert tracing.setup(Settings(_env_file=tmp_path / ".env", data_dir=tmp_path)) is True
    assert fake_mlflow["uri"] == "http://127.0.0.1:5001"
