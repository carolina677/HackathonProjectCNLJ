# Watchtower Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Docker container on port 8080 that attaches to unseen procurement portals, delivers every open tender with documents, notices breakage, repairs itself, and shows all of it in a "wow" dashboard.

**Architecture:** FastAPI + SQLite + an asyncio scheduler. A per-portal pipeline (Fingerprint → Explore → Induce → Verify → Serve → Watch/Heal) turns a URL into a declarative **recipe** that a deterministic engine runs without any model call. Jev makes the cheap judgments, a configurable frontier LLM writes and repairs recipes, and every decision is emitted as an event that drives the Agent Theater over SSE. A Chaos Lab inside the same app generates mutable fake portals with ground truth and a scorer.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, pydantic v2, aiosqlite, httpx, Playwright (Chromium), selectolax, lxml, jsonpath-ng, `typesafe-sdk` (Jev), `anthropic` + `openai` SDKs (one chosen by env), ElevenLabs HTTP API, Jinja2 + htmx + Alpine.js (CDN) for the dashboard, pytest + pytest-asyncio + respx.

**Spec:** `docs/superpowers/specs/2026-11-20-watchtower-design.md` (brief: `docs/brief/Patterno-Track-Description.pdf`). Executors read both.

## Global Constraints

- One container, port `8080`, API + dashboard. `docker build .` must work on `linux/amd64` and build relatively quickly. The image's default command starts API and dashboard on `8080`.
- Every answer is JSON except the dashboard and document bytes. A list is a JSON array. No authentication. Unknown id → `404`.
- Every time is date-time with offset, e.g. `2026-12-09T10:00:00+01:00`. `publicationDate` is a date, e.g. `2026-11-20`.
- Portal `state` ∈ `integrating`, `serving`, `healing`, `failed`. Run `kind` ∈ `read`, `repair`; `result` ∈ `ok`, `failed`, `null` while running.
- Tender `status` ∈ `open`, `closed`, `withdrawn`. `procedureType` ∈ `open`, `restricted`, `negotiated`, `other`. `contractNature` ∈ `works`, `services`, `supplies`. `cpvCodes` are 8-digit strings without check digit (`45000000`, not `45000000-7`). `countryCode` is two letters.
- Required tender fields the portal does not show → `null`. Optional fields not shown → omitted. **A wrong value is worse than a missing one.**
- At most **1 request per second per portal** (per host). No logins, no captcha solving.
- While `healing`, keep serving the last correct tenders. Login/captcha block → `failed` with `reason`.
- Unpack ZIP files. Files over **250 MB** (ZIP included) may be left out.
- `DELETE` → stop calling the portal within a minute.
- Regular reads make **no model call**.
- Model keys come from env vars, named in the README: `LLM_PROVIDER` (`anthropic` | `openai`), `LLM_MODEL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TYPESAFE_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`. Optional: `WATCHTOWER_LAB=1` (enables `/lab`), `WATCHTOWER_DATA=/data`, `READ_INTERVAL_SECONDS=600`.
- Never put a Jev image judgment on the decision path (images are experimental); it is display only.
- Feature freeze at hour 21.

## Review Focus

1. **Deadline shown without offset** (e.g. "15.12.2026 12:00") → interpreted in the portal country's time zone (`DE` → `Europe/Berlin`) and emitted with the correct offset, DST included. Pinned in Task 4.
2. **Same portal URL written differently** (trailing slash, fragment, upper-case host) → same `id`. Pinned in Task 2.
3. **Huge or nested ZIP** → streamed to disk, never held in memory; files > 250 MB skipped; nested ZIPs unpacked one level; path traversal (`../`) in ZIP entries rejected. Pinned in Task 5.
4. **Captcha shown on the list page but documents download without it** → portal is not `failed`. Pinned in Task 8.
5. **Portal deleted or refreshed while a run is in progress** → delete stops all calls within 60 s; refresh during a running read returns that read's `runId`. Pinned in Task 3.

---

## File Structure

```
Dockerfile, pyproject.toml, README.md, .env.example
watchtower/
  config.py          env → Settings
  models.py          pydantic API models + enums (the contract)
  db.py              SQLite schema + async repository
  urls.py            URL normalisation, host key
  app.py             FastAPI factory, API routes, mounts dashboard + lab
  scheduler.py       per-host rate limiter, job queue, periodic reads, clock job
  pipeline.py        attach / read / repair orchestration + state machine
  events.py          in-process event bus + SSE stream
  cost.py            cost ledger (per portal, per provider)
  recipe/dsl.py      Recipe pydantic schema
  recipe/engine.py   run_recipe(recipe, fetcher) -> list[RawTender]
  recipe/normalize.py dates/tz, CPV, enums, country
  documents.py       download, hash, unzip, store, serve
  fetch.py           Fetcher: httpx first, Playwright when needed, network capture, snapshots
  judge/llm.py       provider-agnostic complete_json()
  judge/jev.py       Jev wrapper with LLM fallback
  brain/fingerprint.py  platform signatures → recipe template
  brain/explore.py   page typing, summary resolution, link ranking, block detection
  brain/induce.py    LLM writes Recipe from samples
  brain/verify.py    rule checks, select-don't-generate, field nouls
  brain/health.py    post-read health check
  brain/heal.py      repair: patch recipe from old/new snapshot
  adapters/          one module per known platform (recipe templates)
  briefing.py        morning briefing text + ElevenLabs TTS
  dashboard/routes.py, templates/*.html, static/*
  lab/app.py         Chaos Lab portals (FastAPI sub-app at /lab)
  lab/generator.py   synthetic tenders + ground truth
  lab/mutations.py   mutation catalogue
  lab/scorer.py      score our API vs ground truth (CLI)
tests/  mirrors the package; tests/conftest.py has fixtures
testset/portals.yaml real-portal test set (P3)
```

## Ownership and order

| Owner | Tasks | Starts |
|---|---|---|
| P1 Core | 1, 2, 3, 4, 5, 12 | hour 0 |
| P2 Brain | 6, 7, 8, 9, 10, 11 | hour 0 (after Task 1 pushed) |
| P3 Lab | 13, 14, 15 | hour 0 (after Task 1 pushed) |
| P4 Show | 16, 17, 18, 19 | hour 1 (after Task 2 pushed) |

Task 1 lands first (≤ 45 min) so everyone codes against the same models. Task 12 is the integration point at hour ~8. Task 19 is everyone, hours 21–24.

---

### Task 1: Scaffold, contract models, Docker (P1)

**Files:**
- Create: `pyproject.toml`, `Dockerfile`, `.env.example`, `.gitignore`, `watchtower/__init__.py`, `watchtower/config.py`, `watchtower/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `Settings` (pydantic-settings) with fields `llm_provider: str = "anthropic"`, `llm_model: str`, `typesafe_api_key: str | None`, `elevenlabs_api_key: str | None`, `elevenlabs_voice_id: str | None`, `lab: bool = False`, `data_dir: Path = Path("/data")`, `read_interval_seconds: int = 600`; `get_settings() -> Settings`.
  - Enums `PortalState`, `RunKind`, `RunResult`, `TenderStatus`, `ProcedureType`, `ContractNature` with the exact values from Global Constraints.
  - `Document(fileName: str, sizeBytes: int, sha256: str, path: str)`
  - `Place(postalCode: str | None, city: str | None, countryCode: str | None)`
  - `Tender` with required fields `id, url, title, buyerName, publicationDate, submissionDeadline, status, documents, lastChangedAt` (required-but-nullable: `title, buyerName, publicationDate, submissionDeadline` typed `X | None`) and optional `referenceNumber, description, procedureType, contractNature, cpvCodes, placeOfPerformance`. Serialised with `model_dump(mode="json", exclude_unset=True)` semantics: optional fields omitted when not set, required ones always present (null allowed).
  - `PortalOut(id, url, state, reason, tenderCount, lastSuccessAt: datetime | None)`, `RunOut(id, kind, startedAt, finishedAt: datetime | None, result: RunResult | None)`.
  - Helper `tender_json(t: Tender) -> dict` that applies the omit/null rule.

- [ ] **Step 1: `git init`, write `pyproject.toml`** (deps from Tech Stack; dev deps pytest, pytest-asyncio, respx), `.gitignore` (`.env`, `/data`, `__pycache__`).
- [ ] **Step 2: Write the failing tests**

```python
def test_optional_fields_omitted_required_null():
    t = Tender(id="A1", url="https://p/1", title=None, buyerName="Stadt Aachen",
               publicationDate=date(2026,11,20),
               submissionDeadline=datetime(2026,12,9,10,0,tzinfo=ZoneInfo("Europe/Berlin")),
               status=TenderStatus.open, documents=[], lastChangedAt=datetime(2026,11,20,9,tzinfo=timezone.utc))
    j = tender_json(t)
    assert j["title"] is None
    assert "cpvCodes" not in j and "description" not in j
    assert j["submissionDeadline"] == "2026-12-09T10:00:00+01:00"
    assert j["publicationDate"] == "2026-11-20"

def test_cpv_rejects_check_digit():
    with pytest.raises(ValidationError):
        Tender(**BASE, cpvCodes=["45000000-7"])

def test_enum_values_match_brief():
    assert {s.value for s in PortalState} == {"integrating","serving","healing","failed"}
    assert {s.value for s in TenderStatus} == {"open","closed","withdrawn"}
```

- [ ] **Step 3: Run** `pytest tests/test_models.py -v` → FAIL (import error).
- [ ] **Step 4: Implement** `config.py` and `models.py`. `cpvCodes` items validated by regex `^\d{8}$`; datetimes serialise with offset (`isoformat()`), never `Z`-less naive.
- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Dockerfile.** Base `mcr.microsoft.com/playwright/python:v1.49.0-jammy` (pinned; Chromium preinstalled), `pip install .`, `VOLUME /data`, `EXPOSE 8080`, `CMD ["uvicorn","watchtower.app:create_app","--factory","--host","0.0.0.0","--port","8080"]`. `.env.example` lists every env var from Global Constraints.
- [ ] **Step 7: Verify** `docker buildx build --platform linux/amd64 -t watchtower .` completes.
- [ ] **Step 8: Commit** `feat: scaffold, contract models, Dockerfile` and push.

---

### Task 2: Database and API endpoints (P1)

**Files:**
- Create: `watchtower/db.py`, `watchtower/urls.py`, `watchtower/app.py`
- Test: `tests/test_api_contract.py`, `tests/test_urls.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces:
  - `normalize_url(url: str) -> str` (lower-case scheme/host, drop fragment, drop trailing `/` on path, keep query), `host_key(url: str) -> str`.
  - `class Repo` (aiosqlite, file `{data_dir}/watchtower.db`, WAL): `create_portal(url) -> str` (idempotent on normalised URL), `get_portal(id) -> PortalRow | None`, `list_portals() -> list[PortalRow]`, `delete_portal(id)`, `set_state(id, state, reason)`, `set_recipe(id, recipe_json, snapshot_ref)`, `upsert_tenders(portal_id, tenders: list[Tender]) -> int` (returns number changed; bumps `lastChangedAt` only when content hash changes), `list_tenders(portal_id, changed_since: datetime | None) -> list[Tender]`, `mark_missing_closed(portal_id, seen_ids: set[str])`, `start_run(portal_id, kind) -> str`, `finish_run(run_id, result)`, `running_run(portal_id, kind="read") -> str | None`, `list_runs(portal_id) -> list[RunOut]`, `save_document(...)`, `get_document(file_id) -> DocRow | None`.
  - `PortalRow` has `id, url, state, reason, tenderCount, lastSuccessAt, recipe: dict | None, country: str | None, deleted: bool`.
  - `create_app() -> FastAPI` with all routes from spec §5.3; `app.state.repo`, `app.state.scheduler` (Task 3 attaches it; until then a stub with `enqueue(portal_id, kind) -> str`).

- [ ] **Step 1: Write failing tests**

```python
def test_normalize_same_portal():
    assert normalize_url("https://Vergabe.Example.de/list/#top") == normalize_url("https://vergabe.example.de/list")

async def test_post_portal_201_and_idempotent(client):
    r1 = await client.post("/portals", json={"url": "https://x.de/a/"})
    r2 = await client.post("/portals", json={"url": "https://x.de/a"})
    assert r1.status_code == 201 and r1.json()["id"] == r2.json()["id"]

async def test_unknown_id_404(client):
    for m, p in [("GET","/portals/nope"),("DELETE","/portals/nope"),("POST","/portals/nope/refresh"),
                 ("GET","/portals/nope/tenders"),("GET","/portals/nope/runs"),("GET","/files/nope")]:
        assert (await client.request(m, p)).status_code == 404

async def test_portal_entry_shape(client):
    pid = (await client.post("/portals", json={"url":"https://x.de"})).json()["id"]
    e = (await client.get(f"/portals/{pid}")).json()
    assert set(e) == {"id","url","state","reason","tenderCount","lastSuccessAt"}
    assert e["state"] == "integrating" and e["lastSuccessAt"] is None

async def test_refresh_202_returns_run_id(client): ...   # {"runId": str}
async def test_delete_204(client): ...
async def test_changed_since_filters(client, repo): ...  # seed 2 tenders with lastChangedAt t1<t2; ?changedSince between → only t2
async def test_files_returns_bytes(client, repo, tmp_path): ...  # content + Content-Disposition filename
```

- [ ] **Step 2: Run** `pytest tests/test_api_contract.py tests/test_urls.py -v` → FAIL.
- [ ] **Step 3: Implement** `urls.py`, `db.py`, `app.py`. Error body `{"error": "<message>"}`. `GET /files/{id}` returns `FileResponse` with media type from `mimetypes.guess_type(fileName)`. `GET /` returns a placeholder until Task 16.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: sqlite repo and contract API` and push (P4 can start).

---

### Task 3: Scheduler, rate limiter, run lifecycle (P1)

**Files:**
- Create: `watchtower/scheduler.py`, `watchtower/pipeline.py` (state machine + stage hooks only), `watchtower/events.py`
- Modify: `watchtower/app.py` (lifespan starts/stops scheduler)
- Test: `tests/test_scheduler.py`, `tests/test_state_machine.py`

**Interfaces:**
- Produces:
  - `class HostLimiter: async acquire(host: str)` — at most 1 acquisition per host per second (monotonic clock, per-host lock).
  - `class Scheduler(repo, pipeline, settings)`: `start()`, `stop()`, `enqueue(portal_id, kind: RunKind) -> str` (returns existing running read's run id if any), `cancel(portal_id)`. Periodic loop: every portal not `failed`/deleted read every `read_interval_seconds`, staggered by `hash(id) % interval`; `failed` portals retried every 6 × interval. Clock job every 60 s: `status=closed` for open tenders whose `submissionDeadline < now` (updates `lastChangedAt`).
  - `class Pipeline` with methods `attach(portal_id, run_id)`, `read(portal_id, run_id)`, `repair(portal_id, run_id)` and pluggable stage callables (filled in Task 12). State transitions live in `Pipeline.transition(portal_id, to: PortalState, reason: str)` and are validated against the spec §5.2 table; illegal transitions raise.
  - `events.emit(portal_id: str | None, kind: str, data: dict)`; `events.subscribe() -> AsyncIterator[dict]`. Event kinds used later: `state`, `run`, `page`, `judgment`, `recipe`, `verify`, `cost`, `log`.
  - Every network call in the app goes through `Fetcher` (Task 7) which calls `HostLimiter.acquire`. A cancelled portal makes `Fetcher` raise `PortalCancelled`.

- [ ] **Step 1: Write failing tests**

```python
async def test_limiter_one_per_second():
    lim = HostLimiter(); t0 = time.monotonic()
    for _ in range(3): await lim.acquire("a.de")
    assert time.monotonic() - t0 >= 2.0

async def test_refresh_during_read_returns_same_run(sched_with_slow_pipeline):
    r1 = await sched.enqueue(pid, RunKind.read); r2 = await sched.enqueue(pid, RunKind.read)
    assert r1 == r2

async def test_delete_stops_calls_within_60s(sched_with_counting_fetcher):
    await sched.cancel(pid); n = fetcher.calls; await asyncio.sleep(1.5)
    assert fetcher.calls == n

def test_illegal_transition_raises():
    with pytest.raises(ValueError): Pipeline.check_transition(PortalState.failed, PortalState.healing)
    Pipeline.check_transition(PortalState.serving, PortalState.healing)  # ok

async def test_clock_closes_expired(repo): ...  # open tender with deadline in past → closed, lastChangedAt bumped
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Allowed transitions: integrating→serving|failed, serving→healing|serving, healing→serving|failed, failed→integrating (backoff retry). Each run: `repo.start_run` → stage → `repo.finish_run` → `events.emit("run", …)`.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: scheduler, limiter, state machine, events`.

---

### Task 4: Recipe DSL, engine, normalisers (P1)

**Files:**
- Create: `watchtower/recipe/dsl.py`, `watchtower/recipe/engine.py`, `watchtower/recipe/normalize.py`
- Test: `tests/recipe/test_engine.py`, `tests/recipe/test_normalize.py`

**Interfaces:**
- Produces:
  - `Recipe` (pydantic):
    ```
    source: Literal["html","json_api"]
    list_url: str                       # absolute URL or template with {page}/{offset}/{cursor}
    pagination: Pagination              # kind: none|page_param|offset|next_link|cursor; param, start, step, next_selector, cursor_path, max_pages=200
    item: str                           # CSS/XPath (html) or JSONPath (json_api) selecting one tender
    fields: dict[str, FieldRule]        # key = Tender field name
    detail: DetailRule | None           # how to open the detail page and extra FieldRules read there
    documents: list[DocStep]            # chain: each step = {selector|jsonpath, attr, needs_js, follow: bool}
    id_field: str = "id"
    country: str | None                 # ISO-2, drives time zone
    needs_js: bool = False
    ```
    `FieldRule(selector: str, attr: str = "text", regex: str | None, normalizer: Literal["text","date","datetime","cpv_list","country","procedure","nature","status","url","int"])`.
  - `RawTender = dict[str, Any]` (normalised values, document URLs not yet downloaded under key `_doc_urls`).
  - `async run_recipe(recipe: Recipe, fetcher: Fetcher, *, limit: int | None = None) -> list[RawTender]`.
  - Normalisers: `parse_date(s, country) -> date | None`, `parse_deadline(s, country) -> datetime | None` (aware; naive values localised with `COUNTRY_TZ[country]`), `parse_cpv(s) -> list[str]` (8 digits, strips check digit), `map_procedure(s) -> ProcedureType | None`, `map_nature(s) -> ContractNature | None`. Normalisers return `None` instead of guessing.

- [ ] **Step 1: Write failing tests**

```python
def test_deadline_local_time_gets_offset_with_dst():
    assert parse_deadline("15.12.2026 12:00", "DE").isoformat() == "2026-12-15T12:00:00+01:00"
    assert parse_deadline("15.07.2026 12:00", "DE").isoformat() == "2026-07-15T12:00:00+02:00"
    assert parse_deadline("2026-12-15T12:00:00Z", "DE").utcoffset() == timedelta(0)

def test_dates_multilingual():
    assert parse_date("3 décembre 2026", "FR") == date(2026,12,3)
    assert parse_date("03/12/2026", "ES") == date(2026,12,3)

def test_cpv():
    assert parse_cpv("45000000-7, 71200000-0") == ["45000000","71200000"]
    assert parse_cpv("Bauarbeiten") == []

def test_unparseable_returns_none():
    assert parse_deadline("siehe Unterlagen", "DE") is None

async def test_engine_html_pagination(lab_server):   # uses Task 13 fixture; until then a static HTML fixture in tests/fixtures/
    tenders = await run_recipe(LAB_HTML_RECIPE, fetcher_for(lab_server))
    assert len(tenders) == 23 and tenders[0]["id"] == "LAB-0001"

async def test_engine_json_api_offset(lab_server): ...
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** HTML via selectolax (CSS) with lxml fallback when selector starts with `/` (XPath); JSON via jsonpath-ng. `COUNTRY_TZ` covers the EU-29 incl. `DE, FR, ES, NL, PL, AT, BE, IT, PT, …`. Use `dateparser` with `languages` from country for free text dates.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat: recipe DSL, engine, normalisers`.

---

### Task 5: Documents (P1)

**Files:**
- Create: `watchtower/documents.py`
- Test: `tests/test_documents.py`

**Interfaces:**
- Consumes: `Fetcher.stream(url) -> AsyncIterator[bytes]` (+ headers) from Task 7 (use a fake in tests), `Repo.save_document`.
- Produces: `async fetch_documents(portal_id: str, tender_id: str, urls: list[str], fetcher) -> list[Document]` — streams each to `{data_dir}/files/{sha256[:2]}/{sha256}`, computes SHA256 while streaming, aborts and skips when bytes exceed `MAX_BYTES = 250 * 1024**2`, unpacks ZIPs (one nested level) into separate `Document`s, returns entries whose `path` is `/files/{file_id}`. Filename from `Content-Disposition`, else URL path. De-duplicates by sha256.

- [ ] **Step 1: Write failing tests**

```python
async def test_hash_and_path(fake_fetcher): docs = await fetch_documents("p","t",["https://x/a.pdf"], fake_fetcher)
    # sha256 equals hashlib of payload; path startswith "/files/"; fileName == "a.pdf"
async def test_zip_unpacked(fake_fetcher): ...        # zip with 2 pdfs → 2 Documents, not the zip
async def test_nested_zip_one_level(fake_fetcher): ...
async def test_zip_slip_rejected(fake_fetcher): ...    # entry "../evil.txt" not written outside files dir
async def test_oversize_skipped(fake_fetcher_huge): ...  # declared/streamed > 250 MB → [] and no file left on disk
```

- [ ] **Step 2–4:** fail → implement (use `zipfile` on the stored temp file, never in memory) → pass.
- [ ] **Step 5: Commit** `feat: document download, hashing, unzip`.

---

### Task 6: Model clients — LLM, Jev, cost ledger (P2)

**Files:**
- Create: `watchtower/judge/llm.py`, `watchtower/judge/jev.py`, `watchtower/cost.py`
- Test: `tests/judge/test_jev.py`, `tests/judge/test_llm.py`

**Interfaces:**
- Produces:
  - `async complete_json(system: str, user: str, schema: type[BaseModel], *, portal_id: str | None) -> BaseModel` — provider from `LLM_PROVIDER` (`anthropic` uses tool-use/JSON output; `openai` uses structured outputs), model from `LLM_MODEL`; retries 3× with backoff; validates against `schema`; records tokens and $ in `cost`.
  - `class Jev` (wraps `typesafe_sdk.AsyncTypeSafeClient`, model `jev-latest`):
    - `async choice(state: dict, instructions: str, criteria: dict[str, str], *, portal_id) -> ChoiceAnswer(choice: str, probabilities: dict[str, float], confidence: float)`
    - `async nouls(state: dict, questions: dict[str, str], *, portal_id) -> dict[str, float]` (one batched request)
    - `async batch(state: dict, questions: dict[str, Choice|Noul|Score], *, portal_id) -> dict[str, Any]`
    - `async locate(image_png: bytes, target: str) -> Box | None` (header `TypeSafe-Danger-Zone-Images: true`; display only; returns `None` on any error)
    - On Jev error or missing key: falls back to `complete_json` with an equivalent schema and marks the answer `source="llm"`.
    - Every call emits `events.emit(portal_id, "judgment", {question, answer, probabilities, source, ms, usd})`.
  - `cost.record(portal_id, provider, input_tokens, output_tokens, usd)`; `cost.summary(portal_id) -> {"jev_usd", "llm_usd", "total_usd"}`; `cost.projected(portals=4500) -> float`. Jev price constant: `$0.042` per 1M input tokens, output free.

- [ ] **Step 1: Failing tests** (respx-mock `https://api.typesafe.ai/v1/systemone`):

```python
async def test_choice_parses_probabilities(jev_mock): a = await jev.choice({"page": "…"}, "What kind of page is this?", PAGE_TYPES, portal_id="p")
    assert a.choice == "tender_list" and abs(sum(a.probabilities.values()) - 1) < 1e-6
async def test_jev_failure_falls_back_to_llm(jev_500, llm_fake): a = await jev.choice(...); assert a.source == "llm"
async def test_cost_recorded(jev_mock): ...   # usage.input_tokens=1_000_000 → jev_usd == 0.042
async def test_llm_provider_switch(monkeypatch): ...  # LLM_PROVIDER=openai uses openai client
```

- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: llm, jev, cost ledger`.

---

### Task 7: Fetcher — httpx first, Playwright when needed, network capture (P2)

**Files:**
- Create: `watchtower/fetch.py`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `HostLimiter` (Task 3).
- Produces: `class Fetcher(portal_id, limiter, cancelled: Callable[[], bool])`:
  - `async get(url) -> Page(url, final_url, status, html, text, headers)` via httpx (UA `WatchtowerBot/1.0`, timeout 30 s, follows redirects).
  - `async render(url) -> RenderedPage(Page + screenshot_png: bytes, json_responses: list[CapturedJson(url, method, status, body)], links: list[Link(text, href, context)])` via one shared Playwright Chromium; captures every XHR/fetch response with `application/json`.
  - `async get_json(url, params) -> Any`, `async stream(url) -> StreamResponse` (headers + async byte iterator).
  - `needs_js(page: Page) -> bool` heuristic: visible text < 500 chars or body is an SPA root (`<div id="app|root">` with script bundles).
  - `snapshot(page) -> str` stores html/json under `{data_dir}/snapshots/{portal_id}/{ts}.html` and returns the ref.
  - Every request calls `limiter.acquire(host)` first and raises `PortalCancelled` if `cancelled()`.
  - Emits `page` events with url, status and (render only) a downscaled screenshot as base64 JPEG ≤ 1200 px.

- [ ] **Step 1: Failing tests:** `test_render_captures_json_api(lab_server)` (lab SPA portal → at least one captured JSON with a list of tenders), `test_needs_js_spa_true(lab_server)`, `test_cancelled_raises`, `test_limiter_used` (counting limiter sees each call).
- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: fetcher with playwright network capture`.

---

### Task 8: Explore — page typing, summary resolution, link ranking, blocks (P2)

**Files:**
- Create: `watchtower/brain/explore.py`
- Test: `tests/brain/test_explore.py`

**Interfaces:**
- Consumes: `Fetcher`, `Jev`.
- Produces:
  - `PAGE_TYPES = {"tender_list", "tender_detail", "notice_summary", "search_form", "login_wall", "captcha", "error", "other"}` with one-line criteria each.
  - `async classify_page(page, jev) -> ChoiceAnswer` (state: url, title, first 6,000 chars of visible text, list of form fields).
  - `async rank_links(page, goal: Literal["open_list","next_page","detail","documents","platform"], jev, k=5) -> list[tuple[Link, float]]` — one batched Noul request, one question per candidate link (cap 40 candidates, pre-filtered by code).
  - `async explore(start_url, fetcher, jev, max_pages=15) -> Exploration(list_page: RenderedPage | None, detail_pages: list[RenderedPage], json_candidates: list[CapturedJson], blocked: Literal["login","captcha"] | None, docs_reachable: bool, path: list[str])`.
  - Rules: `notice_summary` → follow best `platform` link; `search_form` → submit empty search (GET forms only); `login_wall`/`captcha` on list page → probe best `documents` link of up to 2 tenders; if bytes arrive, `blocked=None` and `docs_reachable=True`.

- [ ] **Step 1: Failing tests** (Jev mocked deterministically per lab page; lab portals from Task 13):

```python
async def test_summary_page_followed_to_platform(lab): ex = await explore(lab.url("summary-redirect"), …); assert ex.list_page.url.startswith(lab.url("ssr"))
async def test_json_api_detected(lab): ex = await explore(lab.url("spa"), …); assert ex.json_candidates
async def test_login_blocked(lab): ex = await explore(lab.url("login"), …); assert ex.blocked == "login"
async def test_captcha_but_docs_download_not_blocked(lab): ex = await explore(lab.url("captcha-open-docs"), …)
    assert ex.blocked is None and ex.docs_reachable
```

- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: explorer`.

---

### Task 9: Induce recipe (P2)

**Files:**
- Create: `watchtower/brain/induce.py`, `watchtower/brain/prompts/induce.md`
- Test: `tests/brain/test_induce.py`

**Interfaces:**
- Consumes: `Exploration`, `complete_json`, `Recipe`, `run_recipe`.
- Produces: `async induce_recipe(ex: Exploration, *, feedback: list[str] | None = None, portal_id) -> Recipe`. Prompt includes: the Recipe JSON schema, the Tender field table from the spec (verbatim), trimmed list-page HTML (strip scripts/styles, collapse repeated rows to 3), up to 2 detail pages, up to 3 JSON samples (truncated to 8 kB each), and `feedback` from a failed verify. Instruction: prefer `json_api` when a captured JSON lists tenders; leave a field out rather than guess. Emits `recipe` event with the recipe JSON.

- [ ] **Step 1: Failing test** (recorded LLM response fixture so it is deterministic): `test_induced_recipe_runs_on_lab_ssr` → `run_recipe(recipe)` returns ≥ 20 tenders with non-null `id`, `title`. Plus an opt-in live test `@pytest.mark.live` that hits the real LLM on lab `ssr` and `spa`.
- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: recipe induction`.

---

### Task 10: Verify — rules, select-don't-generate, field nouls (P2)

**Files:**
- Create: `watchtower/brain/verify.py`
- Test: `tests/brain/test_verify.py`

**Interfaces:**
- Consumes: `RawTender`, detail `Page`s, `Jev`.
- Produces:
  - `rule_check(t: RawTender) -> list[str]` (problems): deadline after publication, CPV format, status/enum values, URL absolute, id non-empty.
  - `async select_value(field: str, page_text: str, current: Any, jev, country: str = "DE") -> Any | None` — regex finds candidates (dates, datetimes, reference-number patterns); Jev `choice` over candidates + `none`; returns the verbatim candidate normalised, or `None` when `none` wins or confidence < 0.6.
  - `async cross_check(t: RawTender, page_text: str, jev) -> dict[str, float]` — one batched Noul request "Is `<value>` the `<field>` of the tender described in this page?" for every non-null field.
  - `async verify_sample(tenders: list[RawTender], pages: dict[str, Page], jev, sample=5) -> VerifyReport(ok: bool, field_scores: dict[str, float], nulled: dict[str, list[str]], problems: list[str])`. Fields with mean P(yes) < 0.5 become `None` for **all** tenders of that portal (stored as a recipe-level mask); `ok` is False if `id`, `url` or `submissionDeadline` fail in > 1 of 5 samples. Emits `verify` event with the per-field heatmap.

- [ ] **Step 1: Failing tests:**

```python
async def test_wrong_deadline_nulled(jev_mock_says_no): rep = await verify_sample([t_with_wrong_deadline], pages, jev)
    assert "submissionDeadline" in rep.nulled and not rep.ok
async def test_select_value_picks_candidate_verbatim(jev_mock_picks_2): assert await select_value("submissionDeadline", "Frist: 15.12.2026 12:00 … Veröffentlicht: 01.12.2026", None, jev) == parse_deadline("15.12.2026 12:00","DE")
async def test_select_value_none_when_absent(jev_mock_none): assert await select_value("referenceNumber", "no numbers here", None, jev) is None
def test_rule_check_deadline_before_publication(): assert rule_check({...}) == ["submissionDeadline before publicationDate"]
```

- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: verification`.

---

### Task 11: Health check and Heal (P2)

**Files:**
- Create: `watchtower/brain/health.py`, `watchtower/brain/heal.py`, `watchtower/brain/prompts/heal.md`
- Test: `tests/brain/test_health.py`, `tests/brain/test_heal.py`

**Interfaces:**
- Produces:
  - `health_check(prev_count: int, tenders: list[RawTender], errors: list[str]) -> Health(ok: bool, reason: str | None)` — fails on: errors, 0 tenders when prev > 0, count < 50 % of prev (prev ≥ 6), > 30 % of a previously non-null required field now null, selectors matching nothing. `reason` is one plain-English sentence for the dashboard and `reason` field.
  - `async spot_check(tender, page, jev) -> bool` (one `cross_check`, all core fields ≥ 0.5) — run on 1 tender per read, only when Jev key present (cheap).
  - `async heal(portal_id, old: Recipe, old_snapshot: str, fetcher, jev, attempts=3) -> Recipe | None` — re-explore, give the LLM old recipe + diff-relevant parts of old and new page + failure reason, ask for a **patched** recipe; run + verify each attempt; return None after `attempts`. Emits `recipe` events with a diff.

- [ ] **Step 1: Failing tests:** `test_count_drop_unhealthy`, `test_zero_is_unhealthy_only_if_prev_positive`, `test_required_field_null_spike`, `test_heal_after_class_rename(lab)` (recorded LLM fixture; lab mutation `rename_classes` → healed recipe returns the same 23 ids).
- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: health check and heal`.

---

### Task 12: Wire the pipeline end to end (P1 + P2, hour ~8)

**Files:**
- Modify: `watchtower/pipeline.py`, `watchtower/app.py`
- Create: `watchtower/brain/fingerprint.py` (interface only; adapters come in Task 15)
- Test: `tests/test_e2e_lab.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `fingerprint(page: Page) -> Recipe | None` (returns adapter template filled with the page's URL, else None).
  - `Pipeline.attach`: fingerprint → else explore → if blocked: `failed` with reason (`"Portal requires a login"` / `"Portal shows a captcha and documents are not reachable without it"`) → induce → run (limit 30) → verify (loop up to 3 with feedback) → `serving` → full read in background → documents in background.
  - `Pipeline.read`: run recipe (no model call) → apply field mask → `select_value` is **not** called on reads → upsert → `mark_missing_closed` → health check (+ spot check) → on fail: `healing` + enqueue `repair`.
  - `Pipeline.repair`: heal → verify → `serving` or `failed` with reason. During repair tenders stay untouched.
  - `lastSuccessAt` updated on every ok read.

- [ ] **Step 1: Failing e2e tests** (lab server, LLM recorded fixtures, Jev mocked or live with `-m live`):

```python
async def test_attach_ssr_reaches_serving(app, lab): pid = post(lab.url("ssr")); await until_state(pid, "serving", 120)
    ts = get_tenders(pid); assert score(ts, lab.truth("ssr")).attach >= 0.95
async def test_mutation_heals(app, lab): ...  # serving → lab.mutate("ssr","rename_classes") → refresh → healing → serving; runs list has a repair ok
async def test_login_portal_failed_with_reason(app, lab): ...  # state failed, "login" in reason
async def test_healing_keeps_last_tenders(app, lab): ...  # during healing GET tenders == before
async def test_read_makes_no_llm_call(app, lab, llm_spy): ...  # after serving, a refresh adds 0 llm calls
```

- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: end-to-end pipeline`.

---

### Task 13: Chaos Lab portals with ground truth (P3)

**Files:**
- Create: `watchtower/lab/app.py`, `watchtower/lab/generator.py`, `watchtower/lab/templates/*.html`
- Modify: `watchtower/app.py` (mount at `/lab` when `WATCHTOWER_LAB=1`); `tests/conftest.py` (`lab_server` fixture: uvicorn on a free port in a thread, yields `Lab` helper with `url(slug)`, `truth(slug)`, `mutate(slug, name)`, `reset(slug)`)
- Test: `tests/lab/test_lab.py`

**Interfaces:**
- Produces: deterministic (`seed`) portals, 23 tenders each, realistic German/French/Spanish content, PDFs generated on the fly (tiny), one ZIP:
  - `ssr` — server-rendered list, page-param pagination (10/page), detail pages, documents on detail page.
  - `spa` — JS app reading `/lab/spa/api/tenders?offset=` JSON.
  - `multistep` — detail → "Vergabeunterlagen" tab → ZIP download.
  - `summary-redirect` — TED-like notice summary linking to `ssr`.
  - `login` — list behind a login form.
  - `captcha-open-docs` — captcha banner on list, but detail/doc URLs work.
  - `truth(slug) -> list[Tender]` exact expected output (deadlines in `Europe/Berlin`, CPV without check digit).
  - Lab state in memory, `POST /lab/{slug}/reset`.

- [ ] **Step 1: Failing tests:** each slug returns 200; `spa` HTML has < 500 chars visible text; `truth("ssr")` has 23 tenders with unique ids; `captcha-open-docs` doc URL returns PDF bytes.
- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: chaos lab portals` and push by **hour 3** (P1/P2 tests depend on it; until then they use static fixtures).

---

### Task 14: Mutations, Break-it endpoint, scorer (P3)

**Files:**
- Create: `watchtower/lab/mutations.py`, `watchtower/lab/scorer.py`
- Test: `tests/lab/test_mutations.py`, `tests/lab/test_scorer.py`

**Interfaces:**
- Produces:
  - `MUTATIONS = {"rename_classes", "reshuffle_layout", "pagination_style", "date_format", "api_path_move", "json_keys_rename", "html_to_json", "withdraw_one", "deadline_passed", "site_down", "add_login"}`; `POST /lab/{slug}/mutate {"name": …}` and `POST /lab/{slug}/break` (random mutation that is repairable) → `{"mutation": name}`. `truth()` updates accordingly (e.g. `withdraw_one` → that tender `withdrawn`).
  - `score(api_tenders: list[dict], truth: list[Tender]) -> Score(attach: float, field_accuracy: dict[str, float], wrong_values: int, missing_values: int)` — wrong values count double, mirroring "a wrong value is worse than a missing one".
  - CLI `python -m watchtower.lab.scorer --api http://localhost:8080 --lab http://localhost:8080/lab` → table of Attach / Notice (state matches reality after each mutation) / Repair per slug. This is the team scoreboard.

- [ ] **Step 1: Failing tests:** each mutation changes the HTML/JSON but not the truth ids (except withdraw/deadline); `score` gives 1.0 for truth vs truth and penalises one wrong deadline more than one null deadline.
- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: lab mutations and scorer`.

---

### Task 15: Real test set, fingerprints, adapters (P3)

**Files:**
- Create: `testset/portals.yaml`, `watchtower/adapters/__init__.py`, `watchtower/adapters/<platform>.py` (3–5), `scripts/run_testset.py`
- Modify: `watchtower/brain/fingerprint.py`
- Test: `tests/adapters/test_fingerprint.py` (saved HTML fixtures, one per adapter)

**Interfaces:**
- Produces:
  - `portals.yaml`: entries `{url, country, software, kind: ssr|spa|multistep|blocked, holdout: bool, notes}`. Requirements from the brief: ≥ 4 countries, ≥ 3 software packages, each with a public list and ≥ 5 open tenders whose documents download without login, mix of server-rendered / JS / multi-step, plus 1–2 blocked portals; mark 3 as `holdout` (never used while developing). Source via TED notices and national sites (oeffentlichevergabe.de, boamp.fr, contrataciondelestado.es, tenderned.nl).
  - Each adapter: `SIGNATURE: list[Callable[[Page], bool]]` and `def template(page: Page) -> Recipe`.
  - `scripts/run_testset.py --api … [--holdout]` posts every portal, waits, prints state, tenderCount, documents, cost per portal.

- [ ] **Step 1: Failing tests:** each fixture HTML is recognised by exactly one adapter; a generic page by none.
- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: test set and platform adapters`.

---

### Task 16: Dashboard morning view + SSE (P4)

**Files:**
- Create: `watchtower/dashboard/routes.py`, `watchtower/dashboard/templates/base.html`, `index.html`, `watchtower/dashboard/static/app.css`, `app.js`
- Modify: `watchtower/app.py` (`GET /` and `GET /events` SSE)
- Test: `tests/dashboard/test_routes.py`

**Interfaces:**
- Consumes: `Repo`, `events.subscribe()`, `cost.summary`.
- Produces: `GET /` renders a grid of portal cards (state colour: integrating = blue, serving = green, healing = amber pulsing, failed = red), tender count, last success, last run, `reason`, "what the system did" (last 3 runs in words). `GET /events` streams JSON events (`text/event-stream`); the page updates cards live with htmx/Alpine (no build step, libs via CDN with pinned versions). Dark theme, readable on a projector (min 16 px, high contrast). An "Add portal" input posts to `/portals`.

- [ ] **Step 1: Failing tests:** `GET /` → 200 HTML containing each portal URL; `GET /events` yields an emitted test event within 2 s.
- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: dashboard morning view`.

---

### Task 17: Agent Theater, cost meter, race panel, Jev Vision, Break-it (P4)

**Files:**
- Create: `watchtower/dashboard/templates/theater.html`, `watchtower/dashboard/static/theater.js`
- Modify: `watchtower/dashboard/routes.py`
- Test: `tests/dashboard/test_theater.py`

**Interfaces:**
- Consumes: event kinds `page`, `judgment`, `recipe`, `verify`, `cost`, `state`, `run`; `Jev.locate`; `complete_json`; lab `POST /lab/{slug}/break`.
- Produces:
  - `GET /portals/{id}/theater` — live timeline: screenshot, Jev question with probability bars, chosen link highlighted, recipe JSON (diff view after repair), per-field confidence heatmap from `verify`, state banner.
  - **Cost meter:** per-portal Jev $ / LLM $ and "4,500 portals ≈ $X" from `cost.projected`.
  - **Race panel:** `POST /portals/{id}/race` runs the same page-type question through Jev and through `complete_json` concurrently; shows ms and $ side by side.
  - **Jev Vision:** when a `page` event has a screenshot, call `Jev.locate(png, "the table or list of tenders")` and `"the link to download tender documents"`; draw boxes as overlay. Errors → no overlay, nothing else changes.
  - **Break it** button on lab portals only → `POST /lab/{slug}/break`, then `POST /portals/{id}/refresh`.

- [ ] **Step 1: Failing tests:** theater page 200 for known id, 404 unknown; race endpoint returns `{"jev": {"ms","usd","answer"}, "llm": {...}}` with both mocked; break button absent for non-lab URLs.
- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: agent theater and wow panels`.

---

### Task 18: ElevenLabs morning briefing (P4)

**Files:**
- Create: `watchtower/briefing.py`
- Modify: `watchtower/dashboard/routes.py`, `index.html` (button + audio player)
- Test: `tests/test_briefing.py`

**Interfaces:**
- Produces:
  - `briefing_text(portals: list[PortalRow], runs_last_24h: list[RunOut]) -> str` — deterministic template, ≤ 70 words: counts by state, portals repaired overnight, each failed portal with its reason. Optional LLM polish behind a flag, never adds facts.
  - `async synthesize(text: str) -> bytes` — `POST https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}` with header `xi-api-key`, body `{"text": …, "model_id": "eleven_multilingual_v2"}`, returns MP3 bytes.
  - `GET /briefing.mp3` (cached 5 min) and `GET /briefing.txt`. Without key → text only, button shows the text.

- [ ] **Step 1: Failing tests:** text for 14 serving / 2 repaired / 1 failed-login contains "14", "2", "login"; synthesize hits the mocked URL with the voice id; missing key → `/briefing.mp3` 404 and `/briefing.txt` 200.
- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `feat: spoken morning briefing`.

---

### Task 19: Harden, README, video, submit (everyone, hours 18–24)

**Files:**
- Create/modify: `README.md`, `scripts/demo_reset.sh`
- Test: full suite + scorer + test set

- [ ] **Step 1: Scoreboard loop (from hour 14):** run `python -m watchtower.lab.scorer` and `scripts/run_testset.py` after every merge; fix the worst slug first.
- [ ] **Step 2: amd64 check:** `docker buildx build --platform linux/amd64 -t watchtower .` then `docker run --platform linux/amd64 -p 8080:8080 --env-file .env watchtower`; run `scripts/run_testset.py --api http://localhost:8080`. Expected: container starts, ≥ 1 real portal `serving`.
- [ ] **Step 3: Holdout run:** `scripts/run_testset.py --holdout` — record numbers for the README; do not tune on holdouts.
- [ ] **Step 4: README (one page):** how it works (pipeline diagram), env vars (exact list from Global Constraints), **cost per portal** (attach and per read, from `cost.summary` averaged over the test set) and projection for 4,500 portals, known limits.
- [ ] **Step 5: Video (≤ 3 min):** script: problem (15 s) → add 3 real portals, Theater live (60 s) → Break it on a lab portal, watch healing (45 s) → login portal fails honestly (15 s) → cost meter + race panel (20 s) → spoken briefing (15 s) → close (10 s). Narration via ElevenLabs.
- [ ] **Step 6: Feature freeze at hour 21; final commit and submission link.**

---

## Self-review notes

- Spec coverage: §5.1 stages → Tasks 8–12, 15; §5.2 → Task 3; §5.3 → Task 2; §5.4 → Tasks 3, 12, 6; §6.1 → 13–14; §6.2–6.3 → 16–17; §6.4 → 18; §7–8 → Ownership + Task 19; §9 risks → Tasks 1, 6, 17, 19.
- LLM provider stays open via `LLM_PROVIDER` / `LLM_MODEL` (Task 6).
