"""Before we serve a portal, we check our own output against the pages it came from.

Rules catch impossible values; Jev cross-checks each field against the page; doubtful fields become null.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from statistics import mean
from typing import Any

from .. import events
from ..judge.jev import JudgeUnavailable
from ..recipe.engine import visible_text
from ..recipe.normalize import clean_text, parse_date, parse_deadline

log = logging.getLogger("watchtower.verify")

CHECK_FIELDS = ["title", "buyerName", "publicationDate", "submissionDeadline", "referenceNumber", "description",
                "cpvCodes"]
CORE_FIELDS = {"title", "submissionDeadline"}
FIELD_MEANING = {
    "title": "title of the tender", "buyerName": "name of the public buyer / contracting authority",
    "publicationDate": "publication date", "submissionDeadline": "deadline for submitting offers (date and time)",
    "referenceNumber": "buyer's reference / file number", "description": "description of what is bought",
    "cpvCodes": "CPV codes",
}
_DATE = (r"\d{1,2}\.\d{1,2}\.\d{4}(?:,?\s+\d{1,2}[:.]\d{2}(?:\s*Uhr)?)?"
         r"|\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
         r"|\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}[:h]\d{2})?"
         r"|\d{1,2}\.?\s+[A-Za-zÄÖÜäöüéûèíóúñ]+\s+\d{4}(?:,?\s+\d{1,2}[:.]\d{2}(?:\s*Uhr)?)?")
CANDIDATE_RX = {
    "submissionDeadline": re.compile(_DATE),
    "publicationDate": re.compile(_DATE),
    "referenceNumber": re.compile(r"\b[A-Z][A-Za-z]{0,9}[-/ ]?\d{2,4}[-/][0-9A-Za-z/-]{1,20}\b"),
}


@dataclass
class VerifyReport:
    ok: bool
    field_scores: dict[str, float] = field(default_factory=dict)
    nulled: dict[str, list[str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    judged: bool = True

    @property
    def mask(self) -> list[str]:
        return sorted(self.nulled)


def rule_check(t: dict) -> list[str]:
    p = []
    if not t.get("id"):
        p.append("id empty")
    url = t.get("url") or ""
    if not re.match(r"^https?://", str(url)):
        p.append("url not absolute")
    pub, dl = t.get("publicationDate"), t.get("submissionDeadline")
    if isinstance(pub, date) and isinstance(dl, datetime) and dl.date() < pub:
        p.append("submissionDeadline before publicationDate")
    for c in t.get("cpvCodes") or []:
        if not re.fullmatch(r"\d{8}", c):
            p.append(f"bad CPV {c}")
    return p


def show(v: Any) -> str:
    if isinstance(v, datetime):
        return f"{v:%d.%m.%Y %H:%M} ({v.isoformat()})"
    if isinstance(v, date):
        return f"{v:%d.%m.%Y} ({v.isoformat()})"
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v)


async def select_value(fld: str, page_text: str, current: Any, jev, country: str = "DE") -> Any | None:
    """Find candidate spans with code, let Jev pick one (or none). The value is copied, never generated."""
    rx = CANDIDATE_RX.get(fld)
    if rx is None:
        return None
    cands: dict[str, dict] = {}
    for m in rx.finditer(page_text):
        val = m.group(0).strip()
        if fld == "submissionDeadline" and parse_deadline(val, country) is None:
            continue
        if fld == "publicationDate" and parse_date(val, country) is None:
            continue
        key = f"c{len(cands)}"
        cands[key] = {"value": val, "context": page_text[max(0, m.start() - 60): m.start()]}
        if len(cands) >= 30:
            break
    if not cands:
        return None
    criteria = {k: f"'{v['value']}' (preceded by: …{v['context']})" for k, v in cands.items()}
    criteria["none"] = "None of these is the right value."
    c = await jev.choice({"field": fld, "candidates": cands, "page": page_text[:8000]},
                         f"Which candidate is the {FIELD_MEANING.get(fld, fld)} of this tender?", criteria, tag=fld)
    if c.choice == "none" or c.choice not in cands or c.confidence < 0.6:
        return None
    raw = cands[c.choice]["value"]
    if fld == "submissionDeadline":
        return parse_deadline(raw, country)
    if fld == "publicationDate":
        return parse_date(raw, country)
    return clean_text(raw)


async def cross_check(t: dict, page_text: str, jev, portal_id: str | None = None) -> dict[str, float]:
    present = {f: t[f] for f in CHECK_FIELDS if t.get(f) not in (None, [], "")}
    if not present:
        return {}
    state = {"page": page_text[:12000], "tender": {f: show(v) for f, v in present.items()}}
    qs = {f"field_{f}": f"Is `{show(v)}` the {FIELD_MEANING[f]} of the tender described on this page?"
          for f, v in present.items()}
    out = await jev.nouls(state, qs, portal_id=portal_id, tag="verify")
    return {k[len("field_"):]: p for k, p in out.items()}


async def verify_sample(tenders: list[dict], pages: dict[str, str], jev, *, sample: int = 5,
                        country: str = "DE", portal_id: str | None = None) -> VerifyReport:
    rep = VerifyReport(ok=False)
    if not tenders:
        rep.problems.append("no tenders extracted")
        rep.feedback.append("The recipe returned no tenders at all.")
        return rep
    ids = [t.get("id") for t in tenders]
    if len(set(ids)) != len(ids):
        rep.problems.append("duplicate tender ids — id_field is not the portal's unique identifier")
    picked = [t for t in tenders if t.get("id") in pages][:sample] or tenders[:sample]
    rule_fail = 0
    for t in picked:
        probs = rule_check(t)
        if probs:
            rule_fail += 1
            rep.problems += [f"{t.get('id')}: {p}" for p in probs]
    scores: dict[str, list[float]] = {}
    failing: dict[str, list[str]] = {}
    try:
        for t in picked:
            text = visible_text(pages[t["id"]]) if t.get("id") in pages else ""
            if not text:
                continue
            res = await cross_check(t, text, jev, portal_id)
            for f, p in res.items():
                scores.setdefault(f, []).append(p)
                if p < 0.5:
                    failing.setdefault(f, []).append(t["id"])
            for f in [f for f in ("submissionDeadline", "publicationDate", "referenceNumber")
                      if res.get(f, 1.0) < 0.5 or (t.get(f) is None and f == "submissionDeadline")]:
                right = await select_value(f, text, t.get(f), jev, country)
                if right is not None:
                    rep.feedback.append(f"{f} for {t['id']} should be '{show(right)}' but the recipe gave "
                                        f"'{show(t.get(f)) if t.get(f) is not None else 'nothing'}'.")
    except JudgeUnavailable as e:
        log.warning("verification without a judge: %s", e)
        rep.judged = False
    rep.field_scores = {f: round(mean(v), 3) for f, v in scores.items()}
    rep.nulled = {f: failing.get(f, []) for f, s in rep.field_scores.items() if s < 0.5}
    core_bad = [f for f in CORE_FIELDS if len(failing.get(f, [])) > 1]
    for f in rep.nulled:
        rep.feedback.append(f"Field {f} did not match the page in {len(failing.get(f, []))}/{len(picked)} samples.")
    rep.feedback += rep.problems[:10]
    rep.ok = (not any("duplicate" in p for p in rep.problems)) and rule_fail <= 1 and not core_bad
    events.emit(portal_id, "verify", {"ok": rep.ok, "field_scores": rep.field_scores, "mask": rep.mask,
                                      "problems": rep.problems[:10], "samples": [t.get("id") for t in picked],
                                      "judged": rep.judged})
    return rep
