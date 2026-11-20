# Watchtower 10/10 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gaps between "strong" and "10/10" on every judging criterion. Notice should catch a broken portal within minutes at zero model cost. Repair, Attach and cost should rest on measured numbers instead of claims. The hand-in should match the brief exactly: a one-page README and a video of at most 3:00.

**Architecture:**
- **Pulse:** a cheap list-only check (`Pipeline.pulse`) runs every 15 min between the 12 h full reads. It uses no Jev and no LLM, so cost stays the same while Notice latency drops from up to 12 h to 15 min.
- **Measurement:** two scripts (`run_testset.py --out`, `repair_matrix.py`) write JSON and a Markdown table. That output becomes `docs/results.md` and the README numbers.
- **Everything else** is docs and recording.

**Tech Stack:** Python 3.12, FastAPI, asyncio Scheduler, pytest (asyncio auto mode), httpx, Chaos Lab (`WATCHTOWER_LAB=1`).

**Spec:** `docs/brief/Patterno-Track-Description.pdf`, the judging criteria and the "What you hand in" section. The base is origin/build at bb1510c. Run `git reset --hard origin/build` locally first, keeping a backup branch, because the local `build` has diverged.

## Global Constraints

- At most one request per second per portal, no logins, no captcha solving (brief, "Be polite").
- "Regular reads should ideally run without a model call." The pulse makes **zero** Jev and LLM calls.
- "No steering": nothing a person does may be needed while the jury runs the system.
- The jury "may call [refresh] every few minutes". The pulse must never start while a read or repair runs.
- `DELETE` means the system stops calling the portal within a minute. This includes the pulse.
- README: "how your system works on one page, which environment variables it needs, and what one portal costs in model usage."
- Video: at most 3:00, "shows your system at work, including the dashboard."
- Holdout portals (`holdout: true` in `testset/portals.yaml`) are run **once** for measurement. No code changes are tuned on what they show.
- Keys come only from `.env`. Never print or commit key values.
- The suite must stay green: `pytest -q` (247 tests on bb1510c).

## Review Focus

1. **Paginated portal.** The pulse reads only the first ~10 items, so a smaller count than `prev_count` must **not** count as broken. Test: `test_pulse_ignores_count_drop`.
2. **Refresh, read or repair already running.** The pulse must not start, and must not create a second run. Test: `test_pulse_skipped_while_run_in_flight`.
3. **Portal deleted.** No pulse after `cancel(pid)`. Test: `test_no_pulse_after_cancel`.
4. **Portal not `serving`** (integrating, healing, failed). No pulse; those states have their own schedule. Test: `test_pulse_only_for_serving`.
5. **Portal down (5xx/timeout).** The pulse triggers one read, which owns the existing backoff. It must not trigger a read on every 5 s tick. Test: `test_unhealthy_pulse_enqueues_one_read`.

---

### Task 1: Pulse, a list-only health check every 15 min (Notice)

**Files:**
- Modify: `watchtower/config.py`. Add `pulse_interval_seconds: int = 900` (env `PULSE_INTERVAL_SECONDS`; `0` turns it off).
- Modify: `watchtower/brain/health.py`. Add `pulse_health`.
- Modify: `watchtower/pipeline.py`. Add `Pipeline.pulse` next to `read` (around line 290).
- Modify: `watchtower/scheduler.py`. Change `_periodic` and add `_pulse`.
- Test: `tests/brain/test_health.py` (append), `tests/test_scheduler.py` (append), `tests/test_e2e_lab.py` (append).

**Interfaces:**
- Produces:
  - `pulse_health(items: list[dict], errors: list[str], prev_fill: dict[str, float] | None) -> Health` in `watchtower/brain/health.py`.
  - `async Pipeline.pulse(pid: str) -> bool`. `True` means healthy or not applicable; `False` means "read now".
  - `Settings.pulse_interval_seconds`.
- Consumes: the existing `health_check`, `fill_rates`, `run_recipe(..., limit=, on_list=)` (on_list returning `False` stops before any detail request), and `Scheduler.enqueue`.

- [ ] **Step 1: Write failing tests for `pulse_health`** in `tests/brain/test_health.py`

```python
def test_pulse_ignores_count_drop():
    items = [{"id": str(i), "title": "t"} for i in range(10)]
    assert pulse_health(items, [], {"title": 1.0}).ok  # prev_count is 80; pulse only sees page one

def test_pulse_flags_layout_change():
    h = pulse_health([], ["list selector matched no items on https://x/"], None)
    assert not h.ok and "learned layout" in h.reason

def test_pulse_flags_empty_titles():
    items = [{"id": str(i), "title": None} for i in range(10)]
    h = pulse_health(items, [], {"title": 1.0})
    assert not h.ok and "title" in h.reason

def test_pulse_transient_is_marked_transient():
    h = pulse_health([], ["HTTP 503 on https://x/"], None)
    assert not h.ok and h.transient
```

- [ ] **Step 2: Run them.** `pytest tests/brain/test_health.py -k pulse -v`. Expected: FAIL with `ImportError: cannot import name 'pulse_health'`.

- [ ] **Step 3: Implement `pulse_health`.** Return `health_check(0, items, errors)` when that is not ok. `prev_count=0` switches off the count-drop rules. Otherwise, if `prev_fill.get("title", 0) >= 0.7` and the title fill among `items` is `< prev_fill["title"] - 0.3`, return `Health(False, "Field title is suddenly empty in most tenders (pulse).")`. Otherwise return `Health(True)`.

- [ ] **Step 4: Run them.** `pytest tests/brain/test_health.py -v`. Expected: PASS.

- [ ] **Step 5: Write failing scheduler tests** in `tests/test_scheduler.py`. Use a fake pipeline with `run` (records calls) and `pulse` (returns a scripted bool and counts calls). Use `settings.model_copy(update={"pulse_interval_seconds": 1})`. The tests set the portal's state with `await repo.set_state(pid, PortalState.serving, None)`.
  - `test_pulse_only_for_serving`: an `integrating` portal gets 0 pulse calls in 2.5 s; a `serving` one gets at least 1.
  - `test_pulse_skipped_while_run_in_flight`: while `SlowPipeline.run` (delay 3 s) runs, `pulse` calls stay 0.
  - `test_unhealthy_pulse_enqueues_one_read`: `pulse` returns `False`. Then exactly one `run(kind=read)` starts within 1 s, and `pulse` is not called again before that read finishes.
  - `test_no_pulse_after_cancel`: after `await s.cancel(pid)`, `pulse` calls stay unchanged for 2 s.
  - `test_pulse_off_when_interval_zero`: with `pulse_interval_seconds=0`, there are 0 pulse calls.

  The `_periodic` loop sleeps 5 s. Make that period a constructor argument, `tick: float = 5.0`, and pass `tick=0.1` in these tests.

- [ ] **Step 6: Run them.** `pytest tests/test_scheduler.py -k pulse -v`. Expected: FAIL. `pulse` is never called, or `TypeError` on `tick`.

- [ ] **Step 7: Implement it in `scheduler.py`.**
  - Add `self._last_pulse: dict[str, float]`.
  - In `_periodic`, after the existing `due` check (a full read wins), add a branch for `p.state == PortalState.serving` with `pulse_interval_seconds > 0`. It applies when `now - max(_last_enqueued.get(pid, _started), _last_pulse.get(pid, 0)) >= pulse_interval_seconds` and the pipeline has `pulse`. It sets `_last_pulse[pid] = now` and `self._tasks[pid] = asyncio.create_task(self._pulse(pid))`.
  - `_pulse` awaits `self.pipeline.pulse(pid)`. On `False`, and when `not self.is_cancelled(pid)`, it pops its own task entry and calls `await self.enqueue(pid, RunKind.read)`.
  - A pulse is **not** a run: no `repo.start_run` and no `run` event, so `/runs` stays "every read and every repair".

- [ ] **Step 8: Run them.** `pytest tests/test_scheduler.py -v`. Expected: PASS (all old and new tests).

- [ ] **Step 9: Write the failing e2e test** `test_pulse_notices_break_without_refresh` in `tests/test_e2e_lab.py`.
  - Build the app with `pulse_interval_seconds=1` and `tick=0.1`. This needs `Settings.scheduler_tick_seconds: float = 5.0` (read by `Scheduler` as the default for `tick`), so `create_app(settings)` needs no new parameter.
  - Attach `ssr` and wait for `serving`, then `lab.mutate("ssr", "rename_classes")`.
  - **Without** calling refresh, `until(..., state in ("healing", "serving") and a repair run exists, 30)`. Then assert `replay.calls` grew only through the repair, and that the pulse itself made 0 LLM calls (compare `replay.calls` before the first repair run starts).

- [ ] **Step 10: Run it.** `pytest tests/test_e2e_lab.py -k pulse -v`. Expected: FAIL (times out, because nothing notices without refresh).

- [ ] **Step 11: Implement `Pipeline.pulse(pid)`.**
  - Return `True` unless the portal exists, `state == serving` and `recipe` is set.
  - Run `run_recipe(recipe, self.fetcher(pid), limit=10, errors=errors, mask=mask, on_list=<capture items, return False>)`.
  - Compute `h = pulse_health(items, errors, p.prev_fill or None)`. If not ok, emit `log` `"Pulse: {h.reason} Reading now."` and return `False`.
  - It calls no judge. The fetcher already enforces the 1 req/s limit.

- [ ] **Step 12: Run the e2e test and the full suite.** `pytest tests/test_e2e_lab.py -k pulse -v`, then `pytest -q`. Expected: PASS, 247 + 10 tests green.

- [ ] **Step 13: Commit.**

```bash
git add watchtower/config.py watchtower/brain/health.py watchtower/pipeline.py watchtower/scheduler.py tests/
git commit -m "feat(notice): 15-minute list-only pulse between 12h reads, zero model calls"
```

---

### Task 2: Repair matrix, one row per repairable mutation (Repair evidence)

**Files:**
- Create: `scripts/repair_matrix.py`
- Test: `tests/test_repair_matrix.py`

**Interfaces:**
- Consumes:
  - Lab HTTP endpoints: `POST /lab/{slug}/reset`, `POST /lab/{slug}/mutate` with body `{"name": "<mutation>"}`, and `GET /lab/{slug}/truth`.
  - `watchtower.lab.scorer.score(api_tenders, truth) -> Score` (`.attach`, `.quality`).
  - `watchtower.lab.mutations.REPAIRABLE`.
- Produces:
  - `render_matrix(rows: list[dict]) -> str`, a Markdown table with the columns `mutation | portal | noticed | repaired | quality before | quality after | seconds | LLM USD`.
  - `async run_matrix(api: str, lab: str, timeout: float) -> list[dict]`.
  - CLI: `python scripts/repair_matrix.py --api http://localhost:8080 [--out docs/results-repair.json]`.

- [ ] **Step 1: Write the failing test** `test_render_matrix_rows_and_header`. Two rows in; check that the header line equals the column list above, there are 2 data rows, `0.97` is formatted as `0.97` and `0.0123` USD as `$0.012`.
- [ ] **Step 2: Run it.** `pytest tests/test_repair_matrix.py -v`. Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement `render_matrix` and `run_matrix`.**
  - Each mutation in `REPAIRABLE` picks a portal slug: `json_keys_rename` and `api_path_move` use `spa`, all others use `ssr`.
  - Per mutation:
    1. Reset the slug and attach it.
    2. Wait for `serving` and score it (before).
    3. Read the cost (`GET /portals/{id}/cost` → `llm_usd`).
    4. Mutate, then **do not refresh**. Wait for `healing`; that proves the pulse noticed it. Then wait for `serving|failed` and score again (after).
    5. Compute the `llm_usd` delta and the seconds from mutate to `serving`.
    6. `DELETE` the portal.
  - Reuse `_wait` from `watchtower/lab/scorer.py` instead of copying it.
- [ ] **Step 4: Run it.** `pytest tests/test_repair_matrix.py -v`. Expected: PASS.
- [ ] **Step 5: Commit.** `git add scripts/repair_matrix.py tests/test_repair_matrix.py && git commit -m "feat: repair matrix script (one row per repairable mutation)"`

---

### Task 3: Test-set results as JSON plus a summary (Attach and Approach evidence)

**Files:**
- Modify: `scripts/run_testset.py`
- Test: `tests/test_run_testset.py`

**Interfaces:**
- Produces:
  - `summarise(rows: list[dict]) -> dict` with keys `serving`, `total`, `tenders`, `documents`, `usd_total`, `usd_per_portal`, `usd_4500`.
  - `--out PATH` writes `{"rows": rows, "summary": summarise(rows)}` as JSON.
  - Each row has `country, url, state, reason, tenders, docs, usd`.

- [ ] **Step 1: Write the failing test** `test_summarise_counts_and_projection`. Use 3 rows (2 serving with usd 0.10 and 0.20, 1 failed with usd 0.30). Expect `serving == 2`, `total == 3`, `usd_per_portal == pytest.approx(0.20)` (the average over **all** attached portals, because failed ones cost money too) and `usd_4500 == pytest.approx(900.0)`.
- [ ] **Step 2: Run it.** `pytest tests/test_run_testset.py -v`. Expected: FAIL (`ImportError`).
- [ ] **Step 3: Implement it.** Refactor `main` to build `rows` first, then print the same table as today, then optionally write `--out`. Make `scripts/` importable for the test: `sys.path` insert in the test, or `scripts/__init__.py` (pick one and ledger it).
- [ ] **Step 4: Run it.** `pytest tests/test_run_testset.py -v && pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -am "feat: run_testset --out writes rows + cost summary"` (add the new test file first).

---

### Task 4: Fix the flaky `test_multistep_zip_documents`

**Files:**
- Test: `tests/test_e2e_lab.py:95`
- Likely: `watchtower/pipeline.py` (`attach` / `_initial_read`, when `serving` is set compared to when documents are stored).

- [ ] **Step 1: Reproduce.** `for i in $(seq 20); do pytest -q tests/test_e2e_lab.py::test_multistep_zip_documents -p no:randomly 2>&1 | tail -1; done`. Expected: a list of 20 results. If all 20 pass, run 20 more with the full `tests/test_e2e_lab.py`, because the flake may need neighbouring tests.
- [ ] **Step 2: If it never fails in 40 runs**, ledger `Ruling: flake not reproducible on bb1510c — team's "integrating until documents stored" change likely fixed it — cost if wrong: one red CI run`, and skip to the commit-free end of this task.
- [ ] **Step 3: If it fails**, use superpowers:systematic-debugging. The hypothesis to test first: `state == serving` becomes visible before the multistep documents are written. Then `score(...).field_accuracy["documents"]` is 0.0, which matches the observed failure. Write the failing assertion that pins the root cause (for example, documents are present at the moment `serving` is first observed), fix it, and run 20× green plus `pytest -q`.
- [ ] **Step 4: Commit** (only if code changed): `git commit -am "fix: serve multistep portal only after its documents are stored"`

---

### Task 5: Measured runs (needs real keys and the user's OK for spend)

There is no code here; this task is the evidence. Before you start, ask the user to approve the spend. Estimate: about $0.10–0.50 per attached portal × 14, plus about 6 repairs. Never print `.env`.

- [ ] **Step 1: Start the server.** Run `docker build -t watchtower . && docker run -p 8080:8080 --env-file .env -e WATCHTOWER_LAB=1 -v wt-measure:/data watchtower`. Expected: `GET /portals` returns `[]`.
- [ ] **Step 2: Run the non-holdout set.** `python scripts/run_testset.py --api http://localhost:8080 --timeout 1800 --out docs/results-testset.json`. Expected: a table and a JSON file; note the line `N/8 serving`.
- [ ] **Step 3: Run the holdout set once.** `python scripts/run_testset.py --api http://localhost:8080 --holdout --timeout 1800 --out docs/results-holdout.json`. Record the result as it is. Holdout failures go into README "Known limits"; they are **not** fixed by tuning on these portals.
- [ ] **Step 4: Run the repair matrix.** `python scripts/repair_matrix.py --api http://localhost:8080 --out docs/results-repair.json`. Expected: 6 rows, "noticed" reached without refresh.
- [ ] **Step 5: Write `docs/results.md`.** Include the three tables, the date, the commit hash, the model (`LLM_MODEL`) and the measured `usd_per_portal`, `usd_4500` and read cost per day.
- [ ] **Step 6: Commit.** `git add docs/results*.{md,json} && git commit -m "docs: measured attach, holdout, repair and cost results"`

---

### Task 6: README on one page

**Files:**
- Modify: `README.md`, cut to **≤ 550 words** (one printed page).
- Create: `docs/how-it-works.md`, which gets everything cut from the README, verbatim. Nothing is lost.

- [ ] **Step 1: Restructure the README** into exactly these sections:
  1. One-paragraph pitch.
  2. How it works: 6 bullets (Attach, Read, **Pulse**, Notice, Repair, States).
  3. Run: `docker build` and `docker run` lines.
  4. Env vars: the table, adding the `PULSE_INTERVAL_SECONDS` row (default 900, 0 = off, zero model calls).
  5. Cost per portal: the **measured** numbers from `docs/results.md`, with "estimate" kept only where nothing was measured.
  6. Results: one line each, linking to `docs/results.md`.
  7. Video link.
  8. Known limits: at most 4 bullets, including holdout failures from Task 5.
- [ ] **Step 2: Verify.** `wc -w README.md`. Expected: ≤ 550. `grep -c "not been measured" README.md` should print 0, unless a number is truly unmeasured.
- [ ] **Step 3: Commit.** `git add README.md docs/how-it-works.md && git commit -m "docs: README fits one page, details moved to docs/how-it-works.md"`

---

### Task 7: Video (at most 3:00)

**Files:** Modify `docs/video-script.md`.

- [ ] **Step 1: Update the script.**
  - The break scene shows the pulse noticing it **without** pressing Refresh. Set `PULSE_INTERVAL_SECONDS=60` for the recording and say so on screen.
  - Show the offline timelapse (`POST /demo/timelapse`) as a fallback for the 4,500-portal story.
  - Show the measured cost number from `docs/results.md`.
  - Keep total narration at ≤ 2:50.
- [ ] **Step 2: Record.** `scripts/demo_reset.sh http://localhost:8080`, then record per the script. Expected: the file length is ≤ 3:00 (`ffprobe -v error -show_entries format=duration -of csv=p=0 video.mp4` < 180).
- [ ] **Step 3: Link the video** in the README (Task 6, section 7) and commit.

---

## Self-review notes

- **Coverage:**
  - Attach: measured, Tasks 3 and 5.
  - Notice: Task 1, plus the e2e test without refresh.
  - Repair: Tasks 2 and 5.
  - Dashboard: the pulse log line appears in the existing event feed and Theater; the UI is already strong.
  - Approach: measured `usd_per_portal` and the pulse's zero cost.
  - Hand-in: Tasks 6 and 7.
- **Ordering:** Tasks 1–4 are code and independent of each other, except that Task 2's "noticed without refresh" needs Task 1. Task 5 needs 1–3. Task 6 needs 5. Task 7 needs 1 and 6.

---

### Task 8 (added from external feedback): a login or captcha page is never stored as a document

Real portals often answer a document link with **HTTP 200 and an HTML login or captcha page**. Before this task, `fetch_documents` stored that page as a "document", and the portal stayed `serving`.

- `watchtower/documents.py`: add `not_a_document(path, headers, size) -> str | None`.
  - It rejects empty files.
  - It rejects HTML pages: `<!doctype html` or `<html` in the first bytes, and no `Content-Disposition: attachment`. The reason names the kind: captcha page, login page, or other HTML page.
  - PDF, ZIP, office and XML files are always kept, whatever the content-type claims.
- `watchtower/brain/health.py`: when no document arrives and the errors name a login or captcha page, the read is unhealthy (not transient). The reason is written for the operator.
- Lab: a new mutation `docs_login`. Document links then answer 200 with a login form.
- Tests:
  - `tests/test_documents.py`: 6 cases.
  - `tests/brain/test_health.py`: 2 cases.
  - `tests/test_pipeline_read.py::test_documents_turning_into_login_page_is_noticed`.

### Task 9 (added from external feedback): no endless healing loop

When a repair produces a recipe but its own verification read fails, `repair()` schedules a read 60 s later. If that read is unhealthy, it triggers another repair, and so on with no limit. Each round costs LLM money, and the portal stays `healing` forever. The brief says "If it cannot be repaired, say that too."

- After `MAX_REPAIR_CYCLES = 3` repairs in a row without a healthy read in between, the portal goes to `failed`. The reason gives the cycle count and the last reason.
- A healthy read resets the counter.
- Tests: in `tests/test_pipeline_read.py`.
