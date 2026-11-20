"""Attach every portal of the test set to a running Watchtower and report state, tenders, documents and cost.

    python scripts/run_testset.py --api http://localhost:8080 [--holdout] [--timeout 900] [--out results.json]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import yaml


def summarise(rows: list[dict]) -> dict:
    """Totals and the model cost per attached portal (failed portals cost money too), projected to 4,500."""
    total = len(rows)
    usd_total = sum(r["usd"] for r in rows)
    per_portal = usd_total / total if total else 0.0
    return {"serving": sum(r["state"] == "serving" for r in rows), "total": total,
            "tenders": sum(r["tenders"] for r in rows), "documents": sum(r["docs"] for r in rows),
            "usd_total": usd_total, "usd_per_portal": per_portal, "usd_4500": per_portal * 4500}


def collect(c: httpx.Client, portals: list[dict], timeout: int) -> list[dict]:
    ids = {p["url"]: c.post("/portals", json={"url": p["url"]}).json()["id"] for p in portals}
    t0 = time.time()
    while time.time() - t0 < timeout:
        states = {u: c.get(f"/portals/{i}").json() for u, i in ids.items()}
        if all(s["state"] in ("serving", "failed") for s in states.values()):
            break
        time.sleep(10)
    rows = []
    for p in portals:
        pid = ids[p["url"]]
        s = c.get(f"/portals/{pid}").json()
        ts = c.get(f"/portals/{pid}/tenders").json() if s["state"] == "serving" else []
        r = c.get(f"/portals/{pid}/cost")
        rows.append({"country": p["country"], "url": p["url"], "state": s["state"], "reason": s.get("reason"),
                     "tenders": s["tenderCount"], "docs": sum(len(t.get("documents") or []) for t in ts),
                     "usd": r.json().get("total_usd", 0.0) if r.status_code == 200 else 0.0})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--file", default="testset/portals.yaml")
    ap.add_argument("--holdout", action="store_true", help="run only the holdout portals")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default=None, help="write rows and summary as JSON")
    a = ap.parse_args()
    portals = [p for p in yaml.safe_load(open(a.file)) if bool(p.get("holdout")) == a.holdout]
    rows = collect(httpx.Client(base_url=a.api, timeout=30), portals, a.timeout)
    print(f"{'country':7} {'state':11} {'tenders':>7} {'docs':>6} {'usd':>7}  url / reason")
    for r in rows:
        print(f"{r['country']:7} {r['state']:11} {r['tenders']:>7} {r['docs']:>6} {r['usd']:>7.3f}  {r['url'][:70]}"
              + (f"\n{'':36}↳ {r['reason']}" if r.get("reason") else ""))
    s = summarise(rows)
    print(f"\n{s['serving']}/{s['total']} serving · ${s['usd_per_portal']:.3f} per portal · "
          f"${s['usd_4500']:.0f} for 4,500 portals")
    if a.out:
        Path(a.out).write_text(json.dumps({"rows": rows, "summary": s}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
