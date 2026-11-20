"""Provider-agnostic frontier LLM for recipe induction and repair. Provider and model come from env."""

import asyncio
import json
import logging
import os
import re
import time
from typing import Awaitable, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .. import cost, events
from ..config import Settings

log = logging.getLogger("watchtower.llm")
T = TypeVar("T", bound=BaseModel)
Backend = Callable[[str, str, dict], Awaitable[tuple[dict, int, int]]]


class LLMUnavailable(Exception):
    pass


# USD per 1M input/output tokens, Anthropic first-party list prices (checked 2026-11-20).
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.0, 20.0),
}
UNKNOWN_PRICE = (8.0, 40.0)  # unknown model: assume a top-tier price so the cost meter never under-reports


def _price(model: str) -> tuple[float, float]:
    """LLM_PRICE_IN / LLM_PRICE_OUT override the table (other providers, negotiated rates)."""
    pin, pout = PRICES.get(model, UNKNOWN_PRICE)
    return float(os.environ.get("LLM_PRICE_IN", pin)), float(os.environ.get("LLM_PRICE_OUT", pout))


def _json_from_text(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


class AnthropicBackend:
    def __init__(self, settings: Settings):
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.llm_model

    async def __call__(self, system: str, user: str, schema: dict) -> tuple[dict, int, int]:
        # No forced tool_choice: current Claude models (Sonnet 5.5, Opus 5.5) reject it with HTTP 400.
        # The schema travels in the prompt; complete_json validates the answer and retries with the errors.
        r = await self.client.messages.create(
            model=self.model, max_tokens=16000,
            system=system + "\nReply with one JSON object matching this schema, and nothing else:\n"
                   + json.dumps(schema),
            messages=[{"role": "user", "content": user}])
        if r.stop_reason == "refusal":
            raise RuntimeError(f"model refused: {getattr(r, 'stop_details', None)}")
        text = "".join(b.text for b in r.content if b.type == "text")
        return _json_from_text(text), r.usage.input_tokens, r.usage.output_tokens


class OpenAIBackend:
    def __init__(self, settings: Settings):
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.llm_model

    async def __call__(self, system: str, user: str, schema: dict) -> tuple[dict, int, int]:
        r = await self.client.chat.completions.create(
            model=self.model, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system + "\nReply with one JSON object matching this schema:\n"
                       + json.dumps(schema)}, {"role": "user", "content": user}])
        u = r.usage
        return _json_from_text(r.choices[0].message.content), u.prompt_tokens, u.completion_tokens


def make_backend(settings: Settings) -> Backend | None:
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return OpenAIBackend(settings)
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicBackend(settings)
    return None


class LLM:
    def __init__(self, settings: Settings, backend: Backend | None = "auto"):
        self.settings = settings
        self.backend = make_backend(settings) if backend == "auto" else backend

    @property
    def available(self) -> bool:
        return self.backend is not None

    async def complete_json(self, system: str, user: str, schema: type[T], *, portal_id: str | None = None,
                            purpose: str = "llm", attempts: int = 3) -> T:
        if not self.backend:
            raise LLMUnavailable("no LLM provider configured")
        js = schema.model_json_schema()
        last: Exception | None = None
        for i in range(attempts):
            t0 = time.monotonic()
            try:
                data, tin, tout = await self.backend(system, user, js)
                pin, pout = _price(self.settings.llm_model)
                usd = tin / 1e6 * pin + tout / 1e6 * pout
                cost.record(portal_id, "llm", tin, tout, usd)
                out = schema.model_validate(data)
                events.emit(portal_id, "llm", {"purpose": purpose, "ms": int((time.monotonic() - t0) * 1000),
                                               "usd": round(usd, 5), "tokens": tin + tout})
                return out
            except ValidationError as e:
                last = e
                user = f"{user}\n\nYour previous answer did not match the schema: {e.errors()[:3]}. Try again."
            except Exception as e:  # network / rate limit
                last = e
                log.warning("LLM call failed (%s), retry %d", e, i + 1)
                await asyncio.sleep(2 ** i)
        raise LLMUnavailable(str(last))
