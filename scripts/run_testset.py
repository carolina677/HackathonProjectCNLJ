"""Attach every portal of the test set to a running Watchtower and report state, tenders, documents and cost.

    python scripts/run_testset.py --api http://localhost:8080 [--holdout] [--timeout 900]
"""

import argparse
import sys
import time

import httpx
import yaml


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--file", default="testset/portals.yaml")
    ap.add_argument("--holdout", action="store_true", help="run only the holdout portals")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    portals = [p for p in yaml.safe_load(open(a.file)) if bool(p.get("holdout")) == a.holdout]
    c = httpx.Client(base_url=a.api, timeout=30)
    ids = {p["url"]: c.post("/portals", json={"url": p["url"]}).json()["id"] for p in portals}
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        states = {u: c.get(f"/portals/{i}").json() for u, i in ids.items()}
        if all(s["state"] in ("serving", "failed") for s in states.values()):
            break
        time.sleep(10)
    print(f"{'country':7} {'state':11} {'tenders':>7} {'docs':>6} {'usd':>7}  url / reason")
    ok = 0
    for p in portals:
        pid = ids[p["url"]]
        s = c.get(f"/portals/{pid}").json()
        ts = c.get(f"/portals/{pid}/tenders").json() if s["state"] == "serving" else []
        docs = sum(len(t.get("documents") or []) for t in ts)
        r = c.get(f"/portals/{pid}/cost")
        usd = r.json().get("total_usd", 0.0) if r.status_code == 200 else 0.0
        ok += s["state"] == "serving"
        print(f"{p['country']:7} {s['state']:11} {s['tenderCount']:>7} {docs:>6} {usd:>7.3f}  {p['url'][:70]}"
              + (f"\n{'':36}↳ {s['reason']}" if s.get("reason") else ""))
    print(f"\n{ok}/{len(portals)} serving")
    return 0


if __name__ == "__main__":
    sys.exit(main())
