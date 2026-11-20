# Watchtower

An agent that attaches to EU procurement portals it has never seen, serves every open tender and its documents on `:8080`, notices when a portal breaks and repairs itself. It also has a morning dashboard with a spoken briefing.

```
POST /portals {url}
   │
   ▼
Fingerprint ──known software (Atexo, AI NetServer, TenderNed, e-Vergabe)──► ready-made recipe ─┐
   │ unknown                                                                       │
   ▼                                                                               │
Explore (Jev: which page is the list, detail, documents; login or captcha?)        │
   │ blocked → failed, with the reason                                             │
   ▼                                                                               │
Induce: the LLM writes a declarative recipe (selectors, pagination, documents)     │
   ▼                                                                               │
Verify: run the recipe on 10 tenders, Jev cross-checks values against the page ◄───┘
   │ up to 3 attempts, with feedback
   ▼
Serve ──every READ_INTERVAL_SECONDS──► Read: deterministic engine, no LLM
                                         │ health check (counts, fill rates, HTTP, layout)
                                         │ + one Jev spot check
                                         ▼ broken
                                       Heal: re-explore, the LLM patches the old recipe
                                       (old snapshot + reason); last good tenders keep serving
```

**Why it scales:** the LLM runs only to learn a recipe and to repair it. Every read after that is plain code. Typed yes/no judgments go to Jev (TypeSafe), which costs far less than an LLM call. All portals share one scheduler. Each host gets at most 1 request per second, with no logins and no captcha solving.

**States:** `integrating → serving ⇄ healing`, or `failed` with a reason. While a portal is `healing`, its last correct tenders stay served. A tender is `closed` once its deadline has passed or the portal stops listing it, and `withdrawn` when the portal marks it cancelled. ZIP files are unpacked (Word, Excel and other office files stay whole), and files over 250 MB are skipped. A tender is only marked `closed` for missing from the list when every list page was read. When a value can't be verified, it is left empty instead of guessed.

**Dashboard (`/`):** one card per portal with its state and story, a live event feed, and a Theater view per portal that shows the agent's steps and recipe diffs. It also has a cost meter, a race panel comparing a Jev answer with an LLM answer, and a spoken morning briefing (ElevenLabs). With `WATCHTOWER_LAB=1`, the built-in Chaos Lab (`/lab`) provides portals you can break on purpose.

## Run

```bash
docker build -t watchtower .
docker run -p 8080:8080 --env-file .env -v watchtower-data:/data watchtower
```

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `LLM_PROVIDER` | yes | `anthropic` (default) or `openai` |
| `LLM_MODEL` | yes | default `claude-sonnet-5-5` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | one of them | key for the chosen provider |
| `TYPESAFE_API_KEY` | yes | Jev judgments. Without it, verification falls back to rule checks only |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | no | spoken briefing (`/briefing.mp3`) |
| `LLM_PRICE_IN`, `LLM_PRICE_OUT` | no | USD per 1M tokens for the cost meter. Defaults are list prices for `claude-sonnet-5-5` ($2/$10), `claude-haiku-5-5` ($0.10/$0.50) and `claude-opus-5-5` ($4/$20) |
| `MLFLOW_TRACKING_URI`, `MLFLOW_EXPERIMENT_NAME` | no | record every LLM and Jev call as an MLflow trace (portal, purpose, tokens, USD). Needs `pip install -e '.[tracing]'`; the Docker image leaves MLflow out to stay small |
| `WATCHTOWER_LAB` | no | `1` mounts the Chaos Lab and the Break it buttons |
| `WATCHTOWER_DATA` | no | data directory, default `/data` |
| `READ_INTERVAL_SECONDS` | no | time between scheduled reads, default `43200` (12 hours). `POST /portals/{id}/refresh` reads at once at any time. Failed portals are retried every 6 intervals, at most once a day |

## Cost per portal

These are **estimates** from the prompt size limits in the code (HTML trimmed to 30,000 + 15,000 characters, at about 3.5 characters per token), using Sonnet 5.5 list prices and Jev at $0.042 per 1M input tokens. They have **not been measured** on a real run yet. The measured value is at `GET /portals/{id}/cost`, and the dashboard shows the average and a projection to 4,500 portals.

| Step | LLM | Jev | Estimated cost |
|---|---|---|---|
| Attach, known software (fingerprint) | 0 calls | a few checks | < $0.001 |
| Attach, unknown portal | 1–3 calls (~14k in / ~1.5k out each) | ~20 checks | $0.04–0.13 |
| Read (every 12 h) | 0 calls | 1 spot check | ~$0.0001 |
| Repair | 1–3 calls | ~20 checks | $0.05–0.15 |

**For 4,500 portals:** attaching all of them costs at most about $600 once. Reads cost about $1 a day at the default 12-hour interval (2 reads per portal), almost all of it Jev spot checks; the cost grows in proportion to a shorter interval (about $65 a day at 10 minutes). Each repair costs about as much as an attach.

## Known limits

- Atexo (FR): the full document set (DCE) sits behind a form. Only the documents that are linked directly are fetched.
- VMPCenter portals (Brandenburg, NRW, DTVP) have no adapter yet, so they go through the LLM path.
- e-Vergabe (evergabe-online.de, federal): only the 10 newest tenders and no documents. Its next-page and download links are tied to a server-side page state that the site's load balancer loses, so they fail over plain HTTP. Tenders are never closed for missing from this partial list; they close at their deadline.
- TenderNed lists about 2,900 open notices. At 1 request per second, the first full read takes hours, so tenders appear progressively.
- Live-tested with `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-5.5` and real Jev: the lab portal was learned and, after Break it, repaired in 39 s, with all 13 fields correct both times. The Claude path is covered by unit tests only; we had no Anthropic key.
- The `linux/amd64` image is built and smoke-tested on every push by `.github/workflows/docker.yml` (GitHub's x86_64 runner): build about 50 s, image about 1.2 GB, Chromium starts, and the API on :8080 answers as the appendix specifies.
- Not yet verified by us: live ElevenLabs and Jev Vision calls, and the holdout portals in `testset/portals.yaml`.

## Development

```bash
uv venv -p 3.12 && uv pip install -e '.[dev]' && .venv/bin/pytest -q
python scripts/run_testset.py --api http://localhost:8080 [--holdout]
scripts/demo_reset.sh http://localhost:8080
```

**Tracing (optional):** `uv pip install -e '.[tracing]'`, start `mlflow server --port 5050 --backend-store-uri sqlite:///.devdata/mlflow.db --artifacts-destination .devdata/mlartifacts`, set `MLFLOW_TRACKING_URI=http://127.0.0.1:5050`. Each LLM call appears as an `llm.<purpose>` trace with the raw OpenAI or Anthropic call underneath; each Jev call as a `jev` trace. With tracing on, the dashboard gets a **Model calls** panel listing the last 30 calls (kind, portal, question or purpose, tokens, time, USD) with a link into MLflow; `GET /traces` returns the same rows as JSON. Tested live: one Break it repair produced 1 LLM trace (gpt-5.5, 5,011 tokens in, 1,529 out) and 10 Jev traces.
