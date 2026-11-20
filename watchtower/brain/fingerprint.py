"""Known platforms get a ready-made recipe instead of induction (cheaper and faster)."""

from ..fetch import Page
from ..recipe.dsl import Recipe


def fingerprint(page: Page) -> Recipe | None:
    from ..adapters import matching

    hits = matching(page)
    return hits[0].template(page) if len(hits) == 1 else None
