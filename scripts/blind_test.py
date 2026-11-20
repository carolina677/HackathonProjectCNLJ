"""Blind test: attach portals that did not exist until this run and score them against ground truth.

    python scripts/blind_test.py --api http://localhost:8080 [--seeds 3] [--seed 123 ...] [--out docs/results-blind.json]

Seeds are drawn with `secrets` at run time, so no recipe, prompt or adapter can have been tuned on these
portals. Each seed is a new layout, language, date format, pagination and URL scheme (watchtower/lab/blind.py).
Needs a server with WATCHTOWER_LAB=1 and real model keys: unknown portals are learned by the LLM.
"""

import argparse
import asyncio
import json
import secrets
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchtower.lab.blind import blind_spec  # noqa: E402
from watchtower.lab.scorer import Score, _wait, score  # noqa: E402
from watchtower.models import Tender  # noqa: E402


def blind_row(seed: int, portal: dict, s: Score | None, docs: int, usd: float, seconds: float) -> dict:
    spec = blind_spec(seed)
    return {"seed": seed, "portal": f"{spec.layout}/{spec.source}/{spec.lang}/{spec.date_fmt}/{spec.paging}",
            "state": portal["state"], "reason": portal.get("reason"),
            "attach": s.attach if s else 0.0, "quality": s.quality if s else None,
            "wrong": s.wrong_values if s else 0, "missing": s.missing_values if s else 0,
            "wrong_examples": s.wrong_examples[:3] if s else [], "docs": docs, "usd": usd, "seconds": seconds}


def summarise(rows: list[dict]) -> dict:
    scored = [r["quality"] for r in rows if r["quality"] is not None]
    n = len(rows)
    return {"serving": sum(r["state"] == "serving" for r in rows), "total": n,
            "attach_avg": sum(r["attach"] for r in rows) / n if n else 0.0,
            "quality_avg": sum(scored) / len(scored) if scored else 0.0,
            "usd_per_portal": sum(r["usd"] for r in rows) / n if n else 0.0}


async def run_one(c: httpx.AsyncClient, api: str, seed: int, timeout: float) -> dict:
    url = f"{api.rstrip('/')}/blind/{seed}/"
    t0 = time.monotonic()
    pid = (await c.post("/portals", json={"url": url})).json()["id"]
    p = await _wait(c, pid, {"serving", "failed"}, timeout)
    truth = [Tender.model_validate(t) for t in (await c.get(f"/blind/{seed}/truth")).json()]
    got = (await c.get(f"/portals/{pid}/tenders")).json() if p["state"] == "serving" else []
    cost = await c.get(f"/portals/{pid}/cost")
    usd = float(cost.json().get("total_usd", 0.0)) if cost.status_code == 200 else 0.0
    s = score(got, truth) if got else None
    docs = sum(len(t.get("documents") or []) for t in got)
    return blind_row(seed, p, s, docs, usd, time.monotonic() - t0)


async def run(api: str, seeds: list[int], timeout: float) -> list[dict]:
    async with httpx.AsyncClient(base_url=api, timeout=60) as c:
        return list(await asyncio.gather(*(run_one(c, api, s, timeout) for s in seeds)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--seeds", type=int, default=3, help="how many fresh random portals")
    ap.add_argument("--seed", type=int, action="append", help="replay a given seed instead (repeatable)")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    seeds = a.seed or [secrets.randbelow(10**9) for _ in range(a.seeds)]
    rows = asyncio.run(run(a.api, seeds, a.timeout))
    print(f"{'seed':>10} {'portal':34} {'state':8} {'attach':>6} {'quality':>7} {'wrong':>5} {'docs':>4} {'usd':>7}")
    for r in rows:
        q = "-" if r["quality"] is None else f"{r['quality']:.2f}"
        print(f"{r['seed']:>10} {r['portal']:34} {r['state']:8} {r['attach']:>6.2f} {q:>7} {r['wrong']:>5} "
              f"{r['docs']:>4} {r['usd']:>7.3f}" + (f"\n{'':11}↳ {r['reason']}" if r["reason"] else "")
              + "".join(f"\n{'':11}✗ {e}" for e in r["wrong_examples"]))
    s = summarise(rows)
    print(f"\n{s['serving']}/{s['total']} serving · attach {s['attach_avg']:.2f} · quality {s['quality_avg']:.2f} · "
          f"${s['usd_per_portal']:.3f} per portal")
    if a.out:
        Path(a.out).write_text(json.dumps({"rows": rows, "summary": s}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
