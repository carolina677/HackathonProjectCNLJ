# Notice Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "Notice" more trustworthy: a short outage is no longer mistaken for a changed portal, the per-read spot check covers different tenders over time, the fill-rate memory survives a restart, and an address without `https://` is accepted.

**Architecture:** Change detection stays where it is: `health_check` and `spot_check` in `watchtower/brain/health.py`, called from `Pipeline.read` in `watchtower/pipeline.py`. A `Health` result gains a `transient` flag. Transient failures schedule a delayed read through the existing follow-up mechanism (now with a delay) instead of a repair. Fill rates move from an in-memory dict to a `prev_fill` column on `portals`.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest (asyncio_mode=auto). Tests run with `.venv/bin/pytest -q`.

**Spec:** `docs/superpowers/specs/2026-11-20-watchtower-design.md` and the brief `docs/brief/Patterno-Track-Description.pdf`.

**Out of scope (team work, not code):** the video, measured costs in the README, the holdout run.

## Global Constraints

- Notice: "The state it reports matches what it delivers." (brief, What we judge)
- "While a portal is `healing`, keep serving its last correct tenders." (brief, appendix)
- `failed`: "Cannot deliver. A person is needed." (brief, What you build)
- "At most one request per second per portal, no logins, no captcha solving." (brief, Rules)
- Regular reads make no LLM call. A Jev outage never marks a portal broken (`spot_check` returns True on `JudgeUnavailable`).
- `POST /portals` answers 201 with `{"id": …}` at once, and the same address twice returns the same id (brief, appendix).
- API errors use the existing shape `{"error": "<words>"}`.
- No new dependencies.

## Review Focus

1. A portal that answers HTTP 503 on every page for 2 minutes and then recovers: it must be `serving` again within about 5 minutes, without any LLM call. Covered in Task 1.
2. A portal that stays down: after the third transient failure in a row it must go to repair. Repair then ends in `failed` with a reason that names the HTTP status. Covered in Task 1.
3. HTTP 404 on the list page is not transient: a moved list page needs a repair. Covered in Task 1.
4. The server restarts in Docker between two reads, and the second read returns a field that is suddenly empty. This must still be noticed. Covered in Task 3.
5. `POST /portals {"url": "vergabe.example.de/ausschreibungen"}` from the jury: it must attach `https://vergabe.example.de/ausschreibungen`, not a broken host. Covered in Task 4.

---

### Task 1: A short outage is not a changed portal

**Files:**
- Modify: `watchtower/brain/health.py` (`Health`, `health_check`)
- Modify: `watchtower/pipeline.py` (`Pipeline.__init__`, `schedule_followup`, `pop_followup`, `read`)
- Modify: `watchtower/scheduler.py:99-116` (`_execute` honours a follow-up delay)
- Test: `tests/brain/test_health.py` (create if missing), `tests/test_pipeline_read.py`, `tests/test_scheduler.py`

**Interfaces:**
- Produces: `Health(ok: bool, reason: str | None = None, transient: bool = False)`.
- Produces: `Pipeline.schedule_followup(pid: str, kind: RunKind, delay: float = 0.0) -> None`.
- Produces: `Pipeline.pop_followup(pid) -> tuple[RunKind, float] | None`. This replaces the old `RunKind | None` return.
- Produces: `RETRY_DELAYS = (60.0, 300.0)` (delay before the 2nd and 3rd attempt) and `MAX_TRANSIENT = 3` in `watchtower/pipeline.py`.

- [ ] **Step 1: Write the failing health tests** in `tests/brain/test_health.py`:

```python
from watchtower.brain.health import health_check

def test_http_503_without_tenders_is_transient():
    h = health_check(20, [], ["HTTP 503 on https://p.example/list"])
    assert not h.ok and h.transient and "503" in h.reason and "not reachable" in h.reason

def test_timeout_without_tenders_is_transient():
    h = health_check(20, [], ["list request failed on https://p.example/api: ReadTimeout"])
    assert not h.ok and h.transient

def test_http_404_is_not_transient():
    h = health_check(20, [], ["HTTP 404 on https://p.example/list"])
    assert not h.ok and not h.transient

def test_layout_change_is_not_transient():
    h = health_check(20, [], ["list selector matched no items on https://p.example/list"])
    assert not h.ok and not h.transient
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/pytest -q tests/brain/test_health.py`
Expected: FAIL. `Health` has no `transient` field.

- [ ] **Step 3: Implement it in `health_check`**

Transient means no tenders were read, and an error matches `HTTP (5\d\d|429)` or contains one of `ConnectError`, `ConnectTimeout`, `ReadTimeout`, `timed out`. Its reason is exactly: `f"The portal is not reachable right now ({detail}). Last tenders stay served; trying again shortly."`, where `detail` is `HTTP 503` or `timeout`. Check for transient before the existing HTTP branch.

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/pytest -q tests/brain/test_health.py`
Expected: PASS.

- [ ] **Step 5: Write the failing pipeline tests** in `tests/test_pipeline_read.py`. Reuse `_served_ssr` and the `flaky` fetcher pattern from `test_http_error_mid_list_closes_nothing`, setting `page.status = 503` for every URL.

```python
async def test_outage_retries_read_instead_of_repair(lab, repo, settings, monkeypatch):
    # every page 503
    result = await pl.read(pid, "r2")
    p = await repo.get_portal(pid)
    assert str(result) == "failed" and p.state == PortalState.healing and "not reachable" in p.reason
    assert pl.pop_followup(pid) == (RunKind.read, 60.0)
    assert len(await repo.list_tenders(pid)) == 23   # still served

async def test_third_outage_in_a_row_goes_to_repair(...):
    # three reads with every page 503
    assert [pl.pop_followup(pid) for _ in ...] == [(RunKind.read, 60.0), (RunKind.read, 300.0), (RunKind.repair, 0.0)]

async def test_recovered_portal_resets_outage_count(...):
    # one 503 read, then a normal read
    assert p.state == PortalState.serving and pl._outages.get(pid, 0) == 0
```

- [ ] **Step 6: Run them and watch them fail**

Run: `.venv/bin/pytest -q tests/test_pipeline_read.py -k outage`
Expected: FAIL. The follow-up is `RunKind.repair`, not a tuple.

- [ ] **Step 7: Implement it in `Pipeline`**

- Add `self._outages: dict[str, int]`.
- In `read`, on unhealthy and transient:
  - increment `_outages[pid]`;
  - if it is below `MAX_TRANSIENT`, `schedule_followup(pid, RunKind.read, RETRY_DELAYS[n - 1])`; otherwise `schedule_followup(pid, RunKind.repair)`;
  - transition to `healing` with the reason in both cases.
- On a healthy read, `_outages.pop(pid, None)`.
- Change `pop_followup` to return the `(kind, delay)` tuple.

- [ ] **Step 8: Write the failing scheduler test** in `tests/test_scheduler.py`. A fake pipeline whose `pop_followup` returns `(RunKind.read, 0.2)` once. Assert that the second run starts no earlier than 0.2 s after the first finished, and that `cancel(pid)` during the wait starts no second run.

- [ ] **Step 9: Implement it in `Scheduler._execute`**

Unpack `(kind, delay)`. If `delay > 0`, `await asyncio.sleep(delay)` inside the same task before `enqueue`, so `cancel()` stops it. Re-check `is_cancelled(pid)` after the sleep.

- [ ] **Step 10: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. `test_failed_portal_with_recipe_gets_another_repair` and the other follow-up users may need the tuple form. Update those assertions, not the behaviour.

- [ ] **Step 11: Commit**

```bash
git add watchtower/brain/health.py watchtower/pipeline.py watchtower/scheduler.py tests/
git commit -m "Notice: a short outage retries the read instead of starting a repair"
```

---

### Task 2: The spot check rotates through tenders

**Files:**
- Modify: `watchtower/brain/health.py` (add `pick_probe`)
- Modify: `watchtower/pipeline.py` (`read`: use `pick_probe` with a per-portal counter)
- Test: `tests/brain/test_health.py`

**Interfaces:**
- Produces: `pick_probe(raw: list[dict], pages: dict[str, str], turn: int) -> dict | None`. This is the tender at index `turn % len(candidates)` among `raw` tenders whose `id` is in `pages`, or `None` if there are none.

- [ ] **Step 1: Write the failing test**

```python
def test_probe_rotates_over_tenders_with_pages():
    raw = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    pages = {"a": "<p>", "c": "<p>"}
    assert [pick_probe(raw, pages, i)["id"] for i in range(3)] == ["a", "c", "a"]
    assert pick_probe(raw, {}, 0) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/pytest -q tests/brain/test_health.py -k probe`
Expected: FAIL with an ImportError.

- [ ] **Step 3: Implement `pick_probe`.** In `read`, replace `next(...)` with `pick_probe(raw, pages, self._probe_turn[pid])`, and increment `self._probe_turn[pid]` after every read that ran a spot check.

- [ ] **Step 4: Run the whole suite and watch it pass**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -am "Notice: the per-read Jev spot check rotates through tenders"
```

---

### Task 3: Fill rates survive a restart

**Files:**
- Modify: `watchtower/db.py` (`SCHEMA` comment, `Repo.init` migration, `PortalRow.prev_fill`, `mark_success`, `set_recipe`)
- Modify: `watchtower/pipeline.py` (drop `self._prev_fill`; read `p.prev_fill`)
- Test: `tests/test_pipeline_read.py`, `tests/test_db.py` (create if missing)

**Interfaces:**
- Produces: `PortalRow.prev_fill: dict[str, float]`, defaulting to `{}`.
- Produces: `Repo.mark_success(pid: str, count: int, when: datetime | None = None, fill: dict[str, float] | None = None) -> None`, which stores `fill` as JSON when it is given.
- Produces: `Repo.set_recipe(...)`, which also clears `prev_fill` to NULL, because a new recipe means new fill rates.

- [ ] **Step 1: Write the failing tests**

```python
async def test_prev_fill_survives_reopen(tmp_path):
    r = Repo(tmp_path); await r.init()
    pid = await r.create_portal("https://p.example/")
    await r.mark_success(pid, 5, fill={"title": 1.0, "buyerName": 0.8})
    await r.close()
    r2 = Repo(tmp_path); await r2.init()
    assert (await r2.get_portal(pid)).prev_fill == {"title": 1.0, "buyerName": 0.8}

async def test_old_database_gets_the_column(tmp_path):
    # create portals table without prev_fill (copy SCHEMA minus the column), then Repo.init() must not fail
```

In `tests/test_pipeline_read.py`, copy `test_failed_read_keeps_served_tenders_but_adds_new_ones` as `test_fill_drop_noticed_after_restart`. Instead of setting `pl._prev_fill`, call `await repo.mark_success(pid, 1, now, fill={...all 1.0})` and build a **new** `Pipeline`. Same assertions.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/pytest -q tests/test_db.py tests/test_pipeline_read.py -k "fill or prev_fill or old_database"`
Expected: FAIL. `mark_success` has no `fill` parameter.

- [ ] **Step 3: Implement it**

- In `Repo.init`, after `executescript`, run `ALTER TABLE portals ADD COLUMN prev_fill TEXT` only when `PRAGMA table_info(portals)` lacks it.
- In the pipeline:
  - pass `fill_rates(raw)` to `mark_success`;
  - use `p.prev_fill or None` in `health_check`;
  - remove `_prev_fill` and its `pop` in `repair`.

- [ ] **Step 4: Run the whole suite and watch it pass**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -am "Notice: fill rates are stored per portal, so a restart does not blind the empty-field check"
```

---

### Task 4: An address without https:// is accepted

**Files:**
- Modify: `watchtower/urls.py` (`normalize_url`)
- Modify: `watchtower/app.py:89-94` (`add_portal`)
- Test: `tests/test_urls.py`, `tests/test_api_contract.py`

**Interfaces:**
- Produces: `normalize_url(url: str) -> str`. Unchanged signature. It now adds `https://` when there is no scheme, and raises `ValueError("not a web address")` when there is no host after that.

- [ ] **Step 1: Write the failing tests**

```python
def test_missing_scheme_gets_https():
    assert normalize_url("vergabe.example.de/ausschreibungen") == "https://vergabe.example.de/ausschreibungen"
    assert normalize_url("www.x.de") == normalize_url("https://www.x.de/")

def test_not_an_address_raises():
    with pytest.raises(ValueError):
        normalize_url("just words")
```

In `tests/test_api_contract.py`:

```python
async def test_post_without_scheme_and_garbage(client):
    a = await client.post("/portals", json={"url": "vergabe.example.de/x"})
    b = await client.post("/portals", json={"url": "https://vergabe.example.de/x"})
    assert a.status_code == 201 and a.json()["id"] == b.json()["id"]
    bad = await client.post("/portals", json={"url": "just words"})
    assert bad.status_code == 422 and "web address" in bad.json()["error"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/pytest -q tests/test_urls.py tests/test_api_contract.py -k "scheme or address or garbage"`
Expected: FAIL.

- [ ] **Step 3: Implement it**

- If `"://"` is not in the stripped input, prefix `https://`.
- A host without a dot that is not `localhost` raises `ValueError`.
- `add_portal` catches it and raises `HTTPException(422, "url must be a web address such as https://portal.example/tenders")`.

- [ ] **Step 4: Run the whole suite and watch it pass**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit and push**

```bash
git commit -am "API: accept a portal address without https://, reject non-addresses with a clear 422"
git push
```

After pushing, the `docker` workflow on GitHub must be green.
