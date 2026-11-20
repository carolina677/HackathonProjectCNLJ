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


def test_price_from_env_file_settings(monkeypatch, tmp_path):
    # Locally the server reads .env through Settings only; os.environ never sees those values.
    from watchtower.config import Settings

    monkeypatch.delenv("LLM_PRICE_IN", raising=False)
    monkeypatch.delenv("LLM_PRICE_OUT", raising=False)
    (tmp_path / ".env").write_text("LLM_PRICE_IN=1.25\nLLM_PRICE_OUT=10\n")
    s = Settings(_env_file=tmp_path / ".env", data_dir=tmp_path)
    assert _price("gpt-5.5", s) == (1.25, 10.0)


def test_known_openai_models_use_published_rates(monkeypatch):
    monkeypatch.delenv("LLM_PRICE_IN", raising=False)
    monkeypatch.delenv("LLM_PRICE_OUT", raising=False)
    assert _price("gpt-5.5") == (2.50, 10.0)
    assert _price("gpt-4o-mini") == (0.15, 0.60)
