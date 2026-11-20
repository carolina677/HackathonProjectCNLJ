"""After every read: is the portal still delivering correctly? Reasons are written for the operator."""

import re
from dataclasses import dataclass

from ..judge.jev import JudgeUnavailable
from .verify import cross_check, evidence_text

REQUIRED_VALUE_FIELDS = ["title", "buyerName", "publicationDate", "submissionDeadline"]
SPOT_CHECK_FIELDS = {"title", "buyerName", "publicationDate", "submissionDeadline"}
_TRANSIENT_HTTP = re.compile(r"(?:HTTP\s+|Server error '|Client error ')(5\d\d|429)\b")
_TRANSIENT_NET = ("ConnectError", "ConnectTimeout", "ReadTimeout", "PoolTimeout",
                  "WriteTimeout", "RemoteProtocolError", "timed out")


@dataclass
class Health:
    ok: bool
    reason: str | None = None
    transient: bool = False


def fill_rates(tenders: list[dict], *, doc_counts: dict[str, int] | None = None) -> dict[str, float]:
    if not tenders:
        return {}
    rates = {f: sum(t.get(f) is not None for t in tenders) / len(tenders) for f in REQUIRED_VALUE_FIELDS}
    if doc_counts is not None:
        rates["documents"] = sum(doc_counts.get(t["id"], 0) > 0 for t in tenders) / len(tenders)
    else:
        rates["documents"] = sum(bool(t.get("documents") or t.get("_doc_urls")) for t in tenders) / len(tenders)
    return rates


def health_check(prev_count: int, tenders: list[dict], errors: list[str],
                 prev_fill: dict[str, float] | None = None, *,
                 doc_counts: dict[str, int] | None = None,
                 doc_errors: list[str] | None = None) -> Health:
    if not tenders:
        for e in errors:
            m = _TRANSIENT_HTTP.search(e)
            if m:
                return Health(
                    False,
                    f"The portal is not reachable right now (HTTP {m.group(1)}). "
                    "Last tenders stay served; trying again shortly.",
                    transient=True,
                )
            if any(tok in e for tok in _TRANSIENT_NET):
                return Health(
                    False,
                    "The portal is not reachable right now (timeout). "
                    "Last tenders stay served; trying again shortly.",
                    transient=True,
                )
    for e in errors:
        m = re.match(r"HTTP (\d{3})", e)
        if m and not tenders:
            return Health(False, f"The portal answered HTTP {m.group(1)} instead of the tender list.")
        if "matched no items" in e:
            return Health(False, "The tender list no longer matches the learned layout (the page changed).")
        if "list request failed" in e and not tenders:  # a late page failing keeps what was read
            return Health(False, "The portal's tender API stopped answering as before.")
    n = len(tenders)
    if n == 0 and prev_count > 0:
        return Health(False, f"The portal returned no tenders; it listed {prev_count} before.")
    if prev_count >= 6 and n < prev_count * 0.5:
        return Health(False, f"Only {n} tenders found; there were {prev_count} before.")
    if doc_errors and doc_counts is not None and sum(doc_counts.values()) == 0:
        if any("HTTP 401" in e or "HTTP 403" in e for e in doc_errors):
            return Health(False, "Document downloads are blocked (HTTP 401/403).", transient=False)
        if any("captcha page instead of a document" in e for e in doc_errors):
            return Health(False, "Document downloads show a captcha instead of the files.", transient=False)
        if any("login page instead of a document" in e for e in doc_errors):
            return Health(False, "Document downloads lead to a login page instead of the files.", transient=False)
    if prev_fill:
        now = fill_rates(tenders, doc_counts=doc_counts)
        for f, before in prev_fill.items():
            if before >= 0.7 and now.get(f, 0) < before - 0.3:
                return Health(False, f"Field {f} is suddenly empty in most tenders ({now.get(f, 0):.0%} filled, "
                                     f"{before:.0%} before).")
    return Health(True)


def pulse_health(items: list[dict], errors: list[str], prev_fill: dict[str, float] | None,
                 list_fields: set[str]) -> Health:
    """Between full reads: does the first list page still look like the learned layout? No model call.

    The pulse sees only the first items, so a smaller count than before is not a sign of breakage. Only fields
    the recipe reads from the list are compared; fields from detail pages are never on the pulse's items."""
    h = health_check(0, items, errors)
    if not h.ok or not items:
        return h
    for f in REQUIRED_VALUE_FIELDS:
        before = (prev_fill or {}).get(f, 0.0)
        if f not in list_fields or before < 0.7:
            continue
        now = sum(t.get(f) is not None for t in items) / len(items)
        if now < before - 0.3:
            return Health(False, f"Field {f} is suddenly empty in most tenders ({now:.0%} filled, "
                                 f"{before:.0%} before; pulse).")
    return Health(True)


def pick_probe(raw: list[dict], pages: dict[str, str], turn: int) -> dict | None:
    cands = [t for t in raw if t.get("id") in pages]
    return cands[turn % len(cands)] if cands else None


async def spot_check(t: dict, page_html: str, jev, portal_id: str | None = None) -> bool:
    """One cheap Jev request on one tender. A judge outage never marks a portal broken."""
    try:
        res = await cross_check(t, evidence_text(page_html), jev, portal_id)
    except JudgeUnavailable:
        return True
    return all(p >= 0.5 for f, p in res.items() if f in SPOT_CHECK_FIELDS)
