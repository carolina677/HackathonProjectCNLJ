"""Platform adapters: one recognised page → a ready recipe, no LLM. Each module has NAME, SIGNATURE, template()."""

from ..fetch import Page
from . import atexo, evergabe, netserver, tenderned

ADAPTERS = [atexo, netserver, tenderned, evergabe]


def matching(page: Page) -> list:
    return [a for a in ADAPTERS if all(sig(page) for sig in a.SIGNATURE)]
