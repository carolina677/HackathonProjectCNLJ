# Watchtower

Give it the URL of an EU procurement portal it has never seen. It serves every open tender and its documents on `:8080`, notices by itself when the portal changes, and repairs itself. Where it cannot help, such as a login wall, it says so instead of guessing.

```
POST /portals {url} → fingerprint (known software → ready recipe, no LLM)
                    → unknown: explore (Jev) → LLM writes a declarative recipe → verify on 10 tenders (Jev)
serving ── pulse every 15 min: list page only, no model call ── read every 12 h: deterministic engine
   │ health check (counts, fill rates, layout, documents are real files, not login pages)
   ▼ broken → healing: the LLM patches the recipe; the last good tenders stay served
             → 3 repairs that don't deliver → failed, with the reason
```

**Why it scales to 4,500 portals:** the LLM runs only to learn a recipe or repair it. Every read after that is plain code. Yes/no checks go to Jev (TypeSafe), which costs far less than an LLM call. Each host gets at most 1 request per second, with no logins and no captcha solving.

## Run

```bash
docker build -t watchtower .
docker run -p 8080:8080 --env-file .env -v watchtower-data:/data watchtower
```

`.env`: `LLM_PROVIDER` (`openai` or `anthropic`), `LLM_MODEL`, the matching API key, and `TYPESAFE_API_KEY`. Optional variables:
- `PULSE_INTERVAL_SECONDS` (900; 0 turns the pulse off)
- `READ_INTERVAL_SECONDS` (43200)
- `WATCHTOWER_LAB=1` mounts the Chaos Lab and the Blind Lab
- `ELEVENLABS_*` for the spoken briefing
- `MLFLOW_TRACKING_URI` for traces

The full list is in `.env.example`.

## Measured (gpt-5.5 + Jev, 20 Nov 2026)

| Test | Result | Model cost |
|---|---|---|
| 4 real portals, known software (DE, FR, NL) | 4/4 serving | $0.001 / portal |
| **Blind portals**: generated from a random seed at run time (layout, language, date format, pagination, URLs, JSON keys); nobody saw them before | run 1: 3/3 serving, all tenders, all 105 PDFs, quality 0.87 · run 2 (after 2 normalizer fixes): 3/3, quality 0.99, 0 wrong values | $0.024–0.028 / portal |
| **Holdout**: 6 real portals, never used during development, run once at tag `holdout-run-1` | **2/6 serving**: LU (204 tenders, 204 docs), FR marches-publics (82 tenders, 182 docs). 4 ended `failed` with a reason | $0.055 / portal (NRW alone $0.26) |
| Same 4 failed portals after 3 general fixes (**no longer blind**) | **NRW and DTVP now serve** (DTVP: 8,320 tenders listed, filled in progressively). ES: firewall rejects automated requests (reported, not bypassed). PL: list only behind a JS search form | $0.06–0.07 / portal |
| Live break (Chaos Lab, no refresh pressed) | layout changes are noticed by the pulse and repaired in about 2 min | a few cents / repair |

Raw data: `docs/results-*.json`. Reproduce:

```bash
python scripts/run_testset.py --api http://localhost:8080 [--holdout]
python scripts/blind_test.py --api http://localhost:8080 --seeds 3
python scripts/repair_matrix.py --api http://localhost:8080
```

The repair matrix needs `PULSE_INTERVAL_SECONDS=60`.

**For 4,500 portals:**
- Attaching them all costs about $110 once, at $0.025 per unknown portal.
- The pulse and the reads cost nothing in model calls, apart from a Jev spot check per read.

## Dashboard (`/`)

- One card per portal with its state and what happened.
- A cost meter with a projection to 4,500 portals.
- **Theater:** the agent's steps for each portal, with a before-vs-after diff of the recipe.
- **⚡ Run demo:** a replay of one night in which three portals break and repair, plus one that needs a person.
- **💥 Break 3 portals live:** real breaks and real repairs, with real model calls.

## Known limits

- Holdout gaps: portals whose list only appears after submitting a JS search form (PL) are not handled yet. Portals whose firewall rejects automated requests (ES) end `failed` with that reason. On NRW, deadline and reference number stay empty rather than guessed.
- The pulse sees only the list page. A change in pagination, or a date that only appears on detail pages, is caught by the 12-hour read instead.
- Logins and captchas are never bypassed. Such portals end `failed` with the reason. Atexo (FR) serves only directly linked documents.
- e-Vergabe (federal DE) serves only the 10 newest tenders and no documents: its paging state is lost behind the site's load balancer.
- TenderNed lists about 2,900 notices. At 1 request per second, the first full read takes hours, so tenders appear progressively.
- The Claude path is covered by unit tests. The live measurements above ran on gpt-5.5.
- CI builds and smoke-tests the `linux/amd64` image on every push (`.github/workflows/docker.yml`).

Development: `uv venv -p 3.12 && uv pip install -e '.[dev]' && .venv/bin/pytest -q`
