"""Break every repairable Chaos Lab mutation once, never press Refresh, and record what Watchtower did.

    python scripts/repair_matrix.py --api http://localhost:8080 [--out docs/results-repair.json] [--timeout 600]

Needs a server started with WATCHTOWER_LAB=1 and real model keys (repairs call the LLM).
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
        cells = [r["mutation"], r["portal"], r["noticed"], r["repaired"], f"{r['quality_before']:.2f}",
                 f"{r['quality_after']:.2f}", f"{r['seconds']:.0f}", f"${r['llm_usd']:.3f}"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


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
            done = await _wait(c, pid, {"serving", "failed"}, timeout)
            after = score((await c.get(f"/portals/{pid}/tenders")).json(), truth)
            rows.append({"mutation": mutation, "portal": slug, "noticed": noticed["state"],
                         "repaired": done["state"], "reason": noticed.get("reason"),
                         "quality_before": before.quality, "quality_after": after.quality,
                         "seconds": time.monotonic() - t0, "llm_usd": await _llm_usd(c, pid) - usd0})
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
