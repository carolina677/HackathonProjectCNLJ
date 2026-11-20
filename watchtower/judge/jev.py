"""Jev (TypeSafe System One): cheap typed judgments with probabilities. Falls back to the LLM if Jev fails."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .. import cost, events, tracing
from ..config import Settings
from .llm import LLM, LLMUnavailable

log = logging.getLogger("watchtower.jev")
MODEL = "jev-latest"


class JudgeUnavailable(Exception):
    pass


@dataclass
class Choice:
    choice: str
    probabilities: dict[str, float]
    confidence: float
    source: str = "jev"
    ms: int = 0


@dataclass
class Box:
    top: float
    bottom: float
    left: float
    right: float
    confidence: float = 0.0


class _LLMChoice(BaseModel):
    choice: str
    confidence: float


class _LLMNouls(BaseModel):
    answers: dict[str, float]


def make_client(settings: Settings):
    if not settings.typesafe_api_key:
        return None
    from typesafe_sdk import AsyncTypeSafeClient

    return AsyncTypeSafeClient(api_key=settings.typesafe_api_key, model=MODEL, timeout=30)


class Jev:
    def __init__(self, settings: Settings, client: Any = "auto", llm: LLM | None = None):
        self.settings = settings
        self.client = make_client(settings) if client == "auto" else client
        self.llm = llm

    def without_llm(self) -> "Jev":
        """Same Jev, no LLM fallback: regular reads must never spend LLM tokens."""
        return Jev(self.settings, client=self.client, llm=None)

    async def _ask(self, state, questions: dict, portal_id, headers=None):
        asked = " · ".join(getattr(q, "instructions", None) or str(k) for k, q in questions.items())
        with tracing.span("jev", {"portal_id": portal_id, "questions": asked[:300]}) as sp:
            t0 = time.monotonic()
            r = await self.client.system_one(state, questions, extra_headers=headers)
            usd = r.usage.input_tokens / 1e6 * cost.JEV_USD_PER_MTOK
            cost.record(portal_id, "jev", r.usage.input_tokens, r.usage.output_tokens, usd)
            if sp is not None:
                sp.set_outputs({"answers": {k: v.choice if getattr(v, "choice", None) is not None
                                            else getattr(v, "noul", None) for k, v in r.answers.items()},
                                "tokens_in": r.usage.input_tokens, "usd": round(usd, 8)})
            return r.answers, int((time.monotonic() - t0) * 1000), usd

    def _emit(self, portal_id, question, answer, probs, source, ms, usd=0.0, extra=None):
        events.emit(portal_id, "judgment", {"question": question, "answer": answer, "probabilities": probs,
                                            "source": source, "ms": ms, "usd": round(usd, 6), **(extra or {})})

    async def choice(self, state: dict, instructions: str, criteria: dict[str, str], *,
                     portal_id: str | None = None, tag: str = "") -> Choice:
        if self.client is not None:
            try:
                from typesafe_sdk import Choice as Q

                answers, ms, usd = await self._ask(state, {"q": Q(instructions=instructions, criteria=criteria)},
                                                   portal_id)
                a = answers["q"]
                c = Choice(a.choice, dict(a.probabilities), float(a.confidence), "jev", ms)
                self._emit(portal_id, instructions, c.choice, c.probabilities, "jev", ms, usd, {"tag": tag})
                return c
            except Exception as e:
                log.warning("Jev choice failed: %s", e)
        if self.llm is None or not self.llm.available:
            raise JudgeUnavailable("neither Jev nor an LLM is available")
        t0 = time.monotonic()
        opts = "\n".join(f"- {k}: {v}" for k, v in criteria.items())
        try:
            r = await self.llm.complete_json(
                "You answer a single classification question about the given state.",
                f"State:\n{state}\n\nQuestion: {instructions}\nOptions:\n{opts}\n"
                "Return the option key as `choice` and your confidence 0..1.", _LLMChoice,
                portal_id=portal_id, purpose="judgment-fallback")
        except LLMUnavailable as e:
            raise JudgeUnavailable(str(e))
        pick = r.choice if r.choice in criteria else next(iter(criteria))
        conf = min(max(r.confidence, 0.0), 1.0)
        rest = (1 - conf) / max(1, len(criteria) - 1)
        probs = {k: (conf if k == pick else rest) for k in criteria}
        ms = int((time.monotonic() - t0) * 1000)
        self._emit(portal_id, instructions, pick, probs, "llm", ms, extra={"tag": tag})
        return Choice(pick, probs, conf, "llm", ms)

    async def nouls(self, state: dict, questions: dict[str, str], *, portal_id: str | None = None,
                    tag: str = "") -> dict[str, float]:
        if not questions:
            return {}
        if self.client is not None:
            try:
                from typesafe_sdk import Noul as Q

                answers, ms, usd = await self._ask(state, {k: Q(instructions=v) for k, v in questions.items()},
                                                   portal_id)
                out = {k: float(answers[k].noul) for k in questions}
                self._emit(portal_id, f"{len(questions)} yes/no checks", None, out, "jev", ms, usd, {"tag": tag})
                return out
            except Exception as e:
                log.warning("Jev nouls failed: %s", e)
        if self.llm is None or not self.llm.available:
            raise JudgeUnavailable("neither Jev nor an LLM is available")
        try:
            r = await self.llm.complete_json(
                "You judge yes/no questions about the given state. Give the probability that the answer is yes.",
                f"State:\n{state}\n\nQuestions:\n" + "\n".join(f"{k}: {v}" for k, v in questions.items()),
                _LLMNouls, portal_id=portal_id, purpose="judgment-fallback")
        except LLMUnavailable as e:
            raise JudgeUnavailable(str(e))
        out = {k: min(max(float(r.answers.get(k, 0.5)), 0.0), 1.0) for k in questions}
        self._emit(portal_id, f"{len(questions)} yes/no checks", None, out, "llm", 0, extra={"tag": tag})
        return out

    async def locate(self, image_png: bytes, target: str, *, portal_id: str | None = None) -> Box | None:
        """Display-only: where on a screenshot is `target`? Never raises, never decides anything."""
        if self.client is None:
            return None
        try:
            from typesafe_sdk import Box as Q, Image

            answers, ms, usd = await self._ask({"screenshot": Image(image_png)},
                                               {"b": Q(instructions=f"Locate {target}.")}, portal_id,
                                               headers={"TypeSafe-Danger-Zone-Images": "true"})
            a = answers["b"]
            return Box(a.box.top, a.box.bottom, a.box.left, a.box.right, float(a.confidence or 0))
        except Exception as e:
            log.info("Jev locate unavailable: %s", e)
            return None
