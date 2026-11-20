"""Score our API output against Chaos Lab ground truth, the way we expect the jury to.

    python -m watchtower.lab.scorer --api http://localhost:8080 [--break]

A wrong value costs more than a missing one (brief: "A wrong value is worse than a missing one").
"""

import argparse
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from ..models import Tender, tender_json

COMPARED = ["url", "title", "buyerName", "publicationDate", "submissionDeadline", "status", "referenceNumber",
            "description", "procedureType", "contractNature", "cpvCodes", "placeOfPerformance", "documents"]


@dataclass
class Score:
    attach: float
    quality: float
    field_accuracy: dict[str, float] = field(default_factory=dict)
    wrong_values: int = 0
    missing_values: int = 0
    wrong_examples: list[str] = field(default_factory=list)


def _same(field_: str, got, want) -> bool:
    if field_ == "submissionDeadline":
        try:
            return datetime.fromisoformat(got) == datetime.fromisoformat(want)
        except (TypeError, ValueError):
            return False
    if field_ == "documents":
        return {(d["fileName"], d["sha256"]) for d in got or []} == {(d["fileName"], d["sha256"]) for d in want}
    if field_ == "cpvCodes":
        return sorted(got or []) == sorted(want or [])
    if field_ == "placeOfPerformance":
        return {k: v for k, v in (got or {}).items() if v} == {k: v for k, v in (want or {}).items() if v}
    return got == want


def score(api_tenders: list[dict], truth: list[Tender]) -> Score:
    got = {t["id"]: t for t in api_tenders}
    correct = wrong = missing = total = 0
    per: dict[str, list[int]] = {}
    examples = []
    matched = 0
    for want_t in truth:
        want = tender_json(want_t)
        g = got.get(want_t.id)
        if g is not None:
            matched += 1
        for f in COMPARED:
            if f not in want or want[f] in (None, []):
                continue
            total += 1
            gv = None if g is None else g.get(f)
            if gv in (None, [], {}) and f != "documents":
                missing += 1
                ok = 0
            elif _same(f, gv, want[f]):
                correct += 1
                ok = 1
            elif f == "documents" and not gv:
                missing += 1
                ok = 0
            else:
                wrong += 1
                ok = 0
                if len(examples) < 8:
                    examples.append(f"{want_t.id}.{f}: got {gv!r}, want {want[f]!r}")
            per.setdefault(f, []).append(ok)
    return Score(attach=matched / len(truth) if truth else 0.0,
                 quality=(correct - wrong) / total if total else 0.0,
                 field_accuracy={f: sum(v) / len(v) for f, v in per.items()},
                 wrong_values=wrong, missing_values=missing, wrong_examples=examples)


# --- CLI ---------------------------------------------------------------------
SLUGS = ["ssr", "spa", "multistep", "summary-redirect", "captcha-open-docs", "login"]


async def _wait(c: httpx.AsyncClient, pid: str, states: set[str], timeout: float) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        p = (await c.get(f"/portals/{pid}")).json()
        if p["state"] in states:
            return p
        await asyncio.sleep(2)
    return (await c.get(f"/portals/{pid}")).json()


async def main(api: str, lab: str, do_break: bool, timeout: float) -> None:
    async with httpx.AsyncClient(base_url=api, timeout=60) as c, httpx.AsyncClient(timeout=60) as lc:
        for slug in SLUGS:
            await lc.post(f"{lab}/{slug}/reset")
        rows = []
        for slug in SLUGS:
            pid = (await c.post("/portals", json={"url": f"{lab}/{slug}/"})).json()["id"]
            p = await _wait(c, pid, {"serving", "failed"}, timeout)
            truth_slug = "ssr" if slug == "summary-redirect" else slug
            truth = [Tender.model_validate(t) for t in (await lc.get(f"{lab}/{truth_slug}/truth")).json()]
            api_t = (await c.get(f"/portals/{pid}/tenders")).json()
            s = score(api_t, truth) if slug != "login" else None
            row = {"slug": slug, "state": p["state"], "reason": p["reason"],
                   "attach": f"{s.attach:.2f}" if s else "-", "quality": f"{s.quality:.2f}" if s else "-",
                   "wrong": s.wrong_values if s else "-", "missing": s.missing_values if s else "-"}
            if slug == "login":
                row["attach"] = "ok" if p["state"] == "failed" else "WRONG"
            if do_break and p["state"] == "serving":
                m = (await lc.post(f"{lab}/{slug}/break")).json()["mutation"]
                await c.post(f"/portals/{pid}/refresh")
                noticed = await _wait(c, pid, {"healing", "failed"}, 60)
                healed = await _wait(c, pid, {"serving", "failed"}, timeout)
                api_t = (await c.get(f"/portals/{pid}/tenders")).json()
                s2 = score(api_t, truth)
                row.update(mutation=m, noticed=noticed["state"], repaired=healed["state"], quality_after=f"{s2.quality:.2f}")
            rows.append(row)
            if s and s.wrong_examples:
                print(f"  {slug} wrong: " + "; ".join(s.wrong_examples[:3]))
        keys = list(dict.fromkeys(k for r in rows for k in r))
        print(" | ".join(keys))
        for r in rows:
            print(" | ".join(str(r.get(k, "")) for k in keys))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--lab", default=None, help="defaults to <api>/lab")
    ap.add_argument("--break", dest="do_break", action="store_true")
    ap.add_argument("--timeout", type=float, default=300)
    a = ap.parse_args()
    asyncio.run(main(a.api, a.lab or a.api.rstrip("/") + "/lab", a.do_break, a.timeout))
