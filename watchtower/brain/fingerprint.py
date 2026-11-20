"""Known platforms (eVergabe, TED, …) get a ready-made recipe instead of induction. Adapters land in Task 15."""

from ..fetch import Page
from ..recipe.dsl import Recipe

ADAPTERS: list = []  # callables Page -> Recipe | None


def fingerprint(page: Page) -> Recipe | None:
    for adapter in ADAPTERS:
        r = adapter(page)
        if r is not None:
            return r
    return None
