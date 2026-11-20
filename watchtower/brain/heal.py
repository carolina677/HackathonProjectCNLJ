"""Repair: the portal changed. Show the LLM the old recipe, how the page looked, how it looks now."""

import json
import logging
from dataclasses import dataclass, field

from .. import events
from ..fetch import PortalCancelled
from ..judge.llm import LLMUnavailable
from ..recipe.dsl import Recipe
from ..recipe.engine import run_recipe
from .explore import explore
from .induce import induce_recipe, trim_html
from .verify import VerifyReport, verify_sample

log = logging.getLogger("watchtower.heal")


@dataclass
class HealReport:
    ok: bool = False
    blocked: str | None = None
    attempts: int = 0
    reason: str | None = None
    verify: VerifyReport | None = None
    exploration_error: str | None = None
    diff: list[str] = field(default_factory=list)
    list_page: object | None = None     # the list page as it looks now (for the before/after view)


def _leaves(v, path: str, out: dict) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            _leaves(x, f"{path}.{k}" if path else k, out)
    elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        for i, x in enumerate(v):
            _leaves(x, f"{path}[{i}]", out)
    else:
        out[path] = v


def recipe_diff(old: Recipe, new: Recipe) -> list[str]:
    """What the repair changed, one readable line per changed setting (e.g. "fields.id.selector: a → b")."""
    a: dict = {}
    b: dict = {}
    _leaves(json.loads(old.model_dump_json()), "", a)
    _leaves(json.loads(new.model_dump_json()), "", b)
    show = lambda v: "—" if v is None else (json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)
    return [f"{k}: {show(a.get(k))} → {show(b.get(k))}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


async def heal(portal_id: str, start_url: str, old: Recipe, old_snapshot: str | None, fetcher, jev, llm, *,
               reason: str, attempts: int = 3, mask: set[str] | None = None) -> tuple[Recipe | None, HealReport]:
    rep = HealReport(reason=reason)
    ex = await explore(start_url, fetcher, jev, portal_id=portal_id)
    rep.list_page = ex.list_page
    if ex.blocked:
        rep.blocked = ex.blocked
        return None, rep
    if ex.list_page is None:
        rep.exploration_error = ex.error
        return None, rep
    extra = (f"## This is a REPAIR\nThe recipe below worked before, then broke: {reason}\n"
             "Patch it: keep what still works, change only what the page change requires.\n"
             f"```json\n{old.model_dump_json(indent=1)}\n```")
    if old_snapshot:
        extra += f"\n\n## How the list page looked when the recipe worked\n```html\n{trim_html(old_snapshot, 8000)}\n```"
    feedback: list[str] | None = None
    for i in range(attempts):
        rep.attempts = i + 1
        try:
            new = await induce_recipe(ex, llm, feedback=feedback, portal_id=portal_id, extra=extra, purpose="repair")
        except LLMUnavailable as e:
            rep.reason = f"LLM unavailable: {e}"
            return None, rep
        pages: dict[str, str] = {}
        errors: list[str] = []
        try:
            sample = await run_recipe(new, fetcher, limit=10, pages=pages, errors=errors)
        except PortalCancelled:
            raise
        except Exception as e:
            sample, errors = [], [f"recipe crashed: {e}"]
        v = await verify_sample(sample, pages, jev, country=new.country or "DE", portal_id=portal_id,
                                expect_docs=ex.docs_reachable)
        rep.verify = v
        if v.ok and sample:
            rep.ok = True
            rep.diff = recipe_diff(old, new)
            events.emit(portal_id, "recipe", {"recipe": json.loads(new.model_dump_json()), "reason": "repaired",
                                              "diff": rep.diff})
            return new, rep
        feedback = (errors[:5] + v.feedback)[:15] or ["The recipe returned nothing usable."]
    return None, rep
