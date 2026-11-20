from watchtower.judge.llm import _price


def test_known_claude_models_use_published_rates(monkeypatch):
    monkeypatch.delenv("LLM_PRICE_IN", raising=False)
    monkeypatch.delenv("LLM_PRICE_OUT", raising=False)
    assert _price("claude-sonnet-5-5") == (2.0, 10.0)
    assert _price("claude-haiku-5-5") == (0.10, 0.50)
    assert _price("claude-opus-5-5") == (4.0, 20.0)


def test_env_overrides_table(monkeypatch):
    monkeypatch.setenv("LLM_PRICE_IN", "1.5")
    monkeypatch.setenv("LLM_PRICE_OUT", "7")
    assert _price("claude-sonnet-5-5") == (1.5, 7.0)


def test_unknown_model_without_env_is_conservative(monkeypatch):
    monkeypatch.delenv("LLM_PRICE_IN", raising=False)
    monkeypatch.delenv("LLM_PRICE_OUT", raising=False)
    pin, pout = _price("some-other-model")
    assert pin >= 4 and pout >= 20
