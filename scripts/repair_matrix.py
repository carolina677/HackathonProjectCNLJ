"""Break every repairable Chaos Lab mutation once, never press Refresh, and record what Watchtower did.

    python scripts/repair_matrix.py --api http://localhost:8080 [--out docs/results-repair.json] [--timeout 600]

Needs a server started with WATCHTOWER_LAB=1, PULSE_INTERVAL_SECONDS=60 and real model keys (repairs call the LLM).
With the default 15-minute pulse a mutation is noticed after up to 900 s, longer than the default timeout.
A mutation the pulse does not notice in time is reported as "not noticed", never as repaired.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchtower.lab.mutations import REPAIRABLE  # noqa: E402
from watchtower.lab.scorer import _wait, score  # noqa: E402
from watchtower.models import Tender  # noqa: E402

COLUMNS = ["mutation", "portal", "noticed", "repaired", "quality before", "quality after", "seconds", "LLM USD"]
SPA_ONLY = {"json_keys_rename", "api_path_move"}


def portal_for(mutation: str) -> str:
    return "spa" if mutation in SPA_ONLY else "ssr"


def render_matrix(rows: list[dict]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        after = "n/a" if r["quality_after"] is None else f"{r['quality_after']:.2f}"
        cells = [r["mutation"], r["portal"], r["noticed"], r["repaired"], f"{r['quality_before']:.2f}",
                 after, f"{r['seconds']:.0f}", f"${r['llm_usd']:.3f}"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def finish_row(row: dict, noticed_state: str, repaired_state: str | None = None,
               quality_after: float | None = None) -> dict:
    """A mutation only counts as repaired if Watchtower noticed it first (healing or failed)."""
    if noticed_state not in ("healing", "failed"):
        return {**row, "noticed": "not noticed", "repaired": "-", "quality_after": None}
    return {**row, "noticed": noticed_state, "repaired": repaired_state or "-", "quality_after": quality_after}


async def _llm_usd(c: httpx.AsyncClient, pid: str) -> float:
    r = await c.get(f"/portals/{pid}/cost")
    return float(r.json().get("llm_usd", 0.0)) if r.status_code == 200 else 0.0


async def run_matrix(api: str, lab: str, timeout: float) -> list[dict]:
    rows = []
    async with httpx.AsyncClient(base_url=api, timeout=60) as c, httpx.AsyncClient(timeout=60) as lc:
        for mutation in REPAIRABLE:
            slug = portal_for(mutation)
            await lc.post(f"{lab}/{slug}/reset")
            truth = [Tender.model_validate(t) for t in (await lc.get(f"{lab}/{slug}/truth")).json()]
            pid = (await c.post("/portals", json={"url": f"{lab}/{slug}/"})).json()["id"]
            await _wait(c, pid, {"serving", "failed"}, timeout)
            before = score((await c.get(f"/portals/{pid}/tenders")).json(), truth)
            usd0 = await _llm_usd(c, pid)
            await lc.post(f"{lab}/{slug}/mutate", json={"name": mutation})
            t0 = time.monotonic()
            noticed = await _wait(c, pid, {"healing", "failed"}, timeout)  # the pulse must find it: no refresh
            base = {"mutation": mutation, "portal": slug, "reason": noticed.get("reason"),
                    "quality_before": before.quality}
            if noticed["state"] in ("healing", "failed"):
                done = await _wait(c, pid, {"serving", "failed"}, timeout)
                after = score((await c.get(f"/portals/{pid}/tenders")).json(), truth).quality
                row = finish_row(base, noticed["state"], done["state"], after)
            else:
                row = finish_row(base, noticed["state"])
            rows.append({**row, "seconds": time.monotonic() - t0, "llm_usd": await _llm_usd(c, pid) - usd0})
            print(render_matrix(rows[-1:]).splitlines()[-1], flush=True)
            await c.delete(f"/portals/{pid}")
            await lc.post(f"{lab}/{slug}/reset")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--lab", default=None, help="defaults to <api>/lab")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rows = asyncio.run(run_matrix(a.api, a.lab or a.api.rstrip("/") + "/lab", a.timeout))
    print("\n" + render_matrix(rows))
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
