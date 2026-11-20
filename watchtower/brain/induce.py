"""The frontier LLM writes a Recipe from what the explorer saw. Called once per portal (and on repair)."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from .. import events
from ..judge.llm import LLM
from ..recipe.dsl import Recipe
from .explore import Exploration

PROMPT = (Path(__file__).parent / "prompts" / "induce.md").read_text()
TLD_COUNTRY = {"de": "DE", "at": "AT", "ch": "CH", "fr": "FR", "be": "BE", "nl": "NL", "lu": "LU", "es": "ES",
               "pt": "PT", "it": "IT", "pl": "PL", "cz": "CZ", "sk": "SK", "hu": "HU", "si": "SI", "hr": "HR",
               "ro": "RO", "bg": "BG", "gr": "GR", "cy": "CY", "mt": "MT", "ie": "IE", "dk": "DK", "se": "SE",
               "fi": "FI", "ee": "EE", "lv": "LV", "lt": "LT", "no": "NO"}
MAX_HTML = 30000


def country_from_url(url: str) -> str | None:
    host = urlsplit(url).hostname or ""
    tld = host.rsplit(".", 1)[-1]
    return TLD_COUNTRY.get(tld)


def trim_html(html: str, limit: int = MAX_HTML) -> str:
    """Strip noise and collapse repeated rows to 3, so the LLM sees structure, not volume."""
    tree = LexborHTMLParser(html)
    for n in tree.css("script, style, svg, noscript, iframe, link, meta, img"):
        n.decompose()
    root = tree.body or tree.root
    for parent in root.css("*"):
        kids = [c for c in parent.iter() if c.tag not in ("-text", "-comment")]
        if len(kids) <= 4:
            continue
        sig = lambda c: (c.tag, c.attributes.get("class"))
        first = sig(kids[0])
        if first[0] in ("td", "th", "dt", "dd", "option", "span", "a", "br"):
            continue  # cells and label/value pairs carry meaning; only rows and blocks repeat
        same = [c for c in kids if sig(c) == first]
        if len(same) > 4:
            extra = len(same) - 3
            for c in same[3:-1]:
                c.decompose()
            same[-1].replace_with(f"<!-- {extra} more similar <{first[0]}> elements -->")
    out = root.html or ""
    out = out.replace("&lt;!--", "<!--").replace("--&gt;", "-->")
    return out[:limit]


def build_prompt(ex: Exploration, feedback: list[str] | None = None, extra: str = "") -> str:
    parts = []
    lp = ex.list_page
    if lp is not None:
        parts.append(f"## List page\nURL: {lp.final_url}\n```html\n{trim_html(lp.html)}\n```")
    for d in ex.detail_pages[:2]:
        parts.append(f"## Detail page\nURL: {d.final_url}\n```html\n{trim_html(d.html, 15000)}\n```")
    for c in ex.json_candidates[:3]:
        parts.append(f"## Captured JSON response ({c.method} {c.url})\n```json\n{json.dumps(c.body, ensure_ascii=False)[:8000]}\n```")
    if ex.doc_hops:
        parts.append("## Document download path found by the explorer\n" + "\n".join(f"- {h}" for h in ex.doc_hops))
    guess = country_from_url(lp.final_url if lp else (ex.path[0] if ex.path else ""))
    parts.append(f"## Country\n{guess or 'unknown — infer it from the content (language, addresses)'}")
    if feedback:
        parts.append("## Feedback from the last verification (fix these)\n" + "\n".join(f"- {f}" for f in feedback))
    if extra:
        parts.append(extra)
    parts.append("Write the Recipe now.")
    return "\n\n".join(parts)


async def induce_recipe(ex: Exploration, llm: LLM, *, feedback: list[str] | None = None,
                        portal_id: str | None = None, extra: str = "", purpose: str = "induce") -> Recipe:
    recipe = await llm.complete_json(PROMPT, build_prompt(ex, feedback, extra), Recipe,
                                     portal_id=portal_id, purpose=purpose)
    if not recipe.country and ex.list_page is not None:
        recipe.country = country_from_url(ex.list_page.final_url)
    if not recipe.country:
        recipe.country = "DE"
    events.emit(portal_id, "recipe", {"recipe": json.loads(recipe.model_dump_json()), "reason": "learned"
                                      if not feedback else "re-learned after verification feedback"})
    return recipe
