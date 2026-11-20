import pytest
from pydantic import BaseModel
from typesafe_sdk import ChoiceAnswer as SdkChoice, NoulAnswer, SystemOneResponse, Usage

from watchtower import cost, events
from watchtower.judge.jev import Jev, JudgeUnavailable
from watchtower.judge.llm import LLM

PAGE_TYPES = {"tender_list": "a list of tenders", "tender_detail": "one tender", "other": "anything else"}


class FakeJevClient:
    def __init__(self, answers=None, fail=False, tokens=1_000_000):
        self.answers, self.fail, self.tokens, self.calls = answers or {}, fail, tokens, []

    async def system_one(self, state, questions, **kw):
        self.calls.append((state, questions, kw))
        if self.fail:
            raise RuntimeError("jev down")
        return SystemOneResponse(model="jev-1.14.0", usage=Usage(input_tokens=self.tokens, output_tokens=3),
                                 answers={k: self.answers[k] for k in questions})


class FakeLLMBackend:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    async def __call__(self, system, user, schema):
        self.calls += 1
        return self.payload, 100, 20


def choice_answer(p):
    best = max(p, key=p.get)
    return SdkChoice(type="choice", choice=best, confidence=p[best], probabilities=p)


@pytest.fixture(autouse=True)
def _reset_cost():
    cost.reset()


async def test_choice_parses_probabilities(settings):
    client = FakeJevClient({"q": choice_answer({"tender_list": 0.9, "tender_detail": 0.07, "other": 0.03})})
    jev = Jev(settings, client=client)
    a = await jev.choice({"page": "…"}, "What kind of page is this?", PAGE_TYPES, portal_id="p")
    assert a.choice == "tender_list" and a.source == "jev"
    assert abs(sum(a.probabilities.values()) - 1) < 1e-6


async def test_nouls_batched_in_one_request(settings):
    client = FakeJevClient({"a": NoulAnswer(type="noul", noul=0.8), "b": NoulAnswer(type="noul", noul=0.1)})
    jev = Jev(settings, client=client)
    got = await jev.nouls({"x": 1}, {"a": "Is A?", "b": "Is B?"}, portal_id="p")
    assert got == {"a": 0.8, "b": 0.1} and len(client.calls) == 1


async def test_jev_failure_falls_back_to_llm(settings):
    backend = FakeLLMBackend({"choice": "tender_detail", "confidence": 0.8})
    jev = Jev(settings, client=FakeJevClient(fail=True), llm=LLM(settings, backend=backend))
    a = await jev.choice({"page": "…"}, "What kind of page?", PAGE_TYPES, portal_id="p")
    assert a.source == "llm" and a.choice == "tender_detail" and backend.calls == 1


async def test_no_key_and_no_llm_raises(settings):
    jev = Jev(settings, client=None, llm=None)
    with pytest.raises(JudgeUnavailable):
        await jev.choice({}, "q", PAGE_TYPES, portal_id="p")


async def test_cost_recorded(settings):
    client = FakeJevClient({"q": choice_answer({"tender_list": 1.0, "tender_detail": 0.0, "other": 0.0})})
    await Jev(settings, client=client).choice({}, "q", PAGE_TYPES, portal_id="p")
    assert cost.summary("p")["jev_usd"] == pytest.approx(0.042)


async def test_judgment_event_emitted(settings):
    client = FakeJevClient({"q": choice_answer({"tender_list": 0.6, "tender_detail": 0.3, "other": 0.1})})
    await Jev(settings, client=client).choice({}, "Page type?", PAGE_TYPES, portal_id="p")
    ev = events.history("p", {"judgment"})[-1]["data"]
    assert ev["question"] == "Page type?" and ev["answer"] == "tender_list" and "ms" in ev


async def test_locate_never_raises(settings):
    jev = Jev(settings, client=FakeJevClient(fail=True))
    assert await jev.locate(b"\x89PNG", "the table") is None


class Out(BaseModel):
    x: int


async def test_llm_validates_and_costs(settings):
    llm = LLM(settings, backend=FakeLLMBackend({"x": 3}))
    r = await llm.complete_json("sys", "user", Out, portal_id="p")
    assert r.x == 3 and cost.summary("p")["llm_usd"] > 0


async def test_llm_provider_switch(settings):
    from watchtower.judge.llm import AnthropicBackend, OpenAIBackend, make_backend

    assert isinstance(make_backend(settings.model_copy(update={"llm_provider": "openai", "openai_api_key": "k"})),
                      OpenAIBackend)
    assert isinstance(make_backend(settings.model_copy(update={"llm_provider": "anthropic",
                                                               "anthropic_api_key": "k"})), AnthropicBackend)
    assert make_backend(settings.model_copy(update={"anthropic_api_key": None, "openai_api_key": None})) is None
    auto_oa = make_backend(settings.model_copy(update={"llm_provider": "anthropic", "llm_model": "claude-sonnet-5-5",
                                                       "anthropic_api_key": None, "openai_api_key": "ok"}))
    assert isinstance(auto_oa, OpenAIBackend) and auto_oa.model == "gpt-5.5"
    auto_ant = make_backend(settings.model_copy(update={"llm_provider": "openai", "llm_model": "gpt-5.5",
                                                        "openai_api_key": None, "anthropic_api_key": "ak"}))
    assert isinstance(auto_ant, AnthropicBackend) and auto_ant.model == "claude-sonnet-5-5"


def test_projection():
    cost.record("a", "jev", 0, 0, 0.01)
    cost.record("b", "llm", 0, 0, 0.03)
    assert cost.projected(4500) == pytest.approx(0.02 * 4500)
