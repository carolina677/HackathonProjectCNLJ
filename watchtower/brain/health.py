"""After every read: is the portal still delivering correctly? Reasons are written for the operator."""

import re
from dataclasses import dataclass

from ..judge.jev import JudgeUnavailable
from ..recipe.engine import visible_text
from .verify import CORE_FIELDS, cross_check

REQUIRED_VALUE_FIELDS = ["title", "buyerName", "publicationDate", "submissionDeadline"]


@dataclass
class Health:
    ok: bool
    reason: str | None = None


def fill_rates(tenders: list[dict]) -> dict[str, float]:
    if not tenders:
        return {}
    return {f: sum(t.get(f) is not None for t in tenders) / len(tenders) for f in REQUIRED_VALUE_FIELDS}


def health_check(prev_count: int, tenders: list[dict], errors: list[str],
                 prev_fill: dict[str, float] | None = None) -> Health:
    for e in errors:
        m = re.match(r"HTTP (\d{3})", e)
        if m and not tenders:
            return Health(False, f"The portal answered HTTP {m.group(1)} instead of the tender list.")
        if "matched no items" in e:
            return Health(False, "The tender list no longer matches the learned layout (the page changed).")
        if "list request failed" in e:
            return Health(False, "The portal's tender API stopped answering as before.")
    n = len(tenders)
    if n == 0 and prev_count > 0:
        return Health(False, f"The portal returned no tenders; it listed {prev_count} before.")
    if prev_count >= 6 and n < prev_count * 0.5:
        return Health(False, f"Only {n} tenders found; there were {prev_count} before.")
    if prev_fill:
        now = fill_rates(tenders)
        for f, before in prev_fill.items():
            if before >= 0.7 and now.get(f, 0) < before - 0.3:
                return Health(False, f"Field {f} is suddenly empty in most tenders ({now.get(f, 0):.0%} filled, "
                                     f"{before:.0%} before).")
    return Health(True)


async def spot_check(t: dict, page_html: str, jev, portal_id: str | None = None) -> bool:
    """One cheap Jev request on one tender. A judge outage never marks a portal broken."""
    try:
        res = await cross_check(t, visible_text(page_html), jev, portal_id)
    except JudgeUnavailable:
        return True
    return all(p >= 0.5 for f, p in res.items() if f in CORE_FIELDS)
