# Watchtower — Design

**Track:** Patterno "Build the Watchtower" · MAIN AI Hackathon 2026, Maastricht · 24 h · team of 4
**Date:** 2026-11-20
**Status:** Draft for team approval

---

## 1. Intent

Build a single Docker container that, given only the URL of a procurement portal it has never seen, attaches to it, delivers every open tender with its documents, notices when delivery breaks, and repairs itself — with nobody steering it during the run.

**Why:** the team's only goal is to **win the track**. Patterno judges objectively on portals we have never seen (their own private test portals that they will mutate, plus real ones). Most teams will paste the brief into an LLM and get the same thing: "LLM writes CSS selectors". That works on their own test pages and breaks on the jury's. We win by being **measurably more robust** on unseen portals and by making that robustness **visible** in a demo that looks like nothing else in the room.

## 2. Goals, non-goals, anti-goals

**Goals**
1. Pass the jury's hidden test: Attach, Notice, Repair on unseen portals (server-rendered, JS apps with JSON APIs, multi-step downloads).
2. Exact API compliance with the brief's appendix (endpoints, status codes, field names, time formats).
3. Very low model cost per portal, with a concrete number in the README. Regular reads make no frontier-model call.
4. A "wow" demo: live Agent Theater, Chaos Lab with a "Break it" button, Jev decision cards and the ElevenLabs morning briefing.

**Non-goals**
- Logins, captcha solving, or anything impolite to real portals (max 1 req/s per portal).
- Supporting every European portal. Breadth comes from the generic explorer, not from writing many adapters.
- Multi-tenant production hardening, auth, or horizontal scaling. We argue scaling in the README.

**Anti-goals (failure even if it "works")**
- Works only on our own test portals.
- Delivers wrong values. A wrong value is worse than a missing one, so uncertain fields become `null`.
- Reports `serving` while the delivered data is wrong (that loses the Notice points).
- A demo that is pretty but hides what the agent does; or one that needs a human to click it through.
- A Docker image that does not build on linux/amd64 or takes forever.

## 3. Constraints (from the brief)

- One container, port 8080, API + dashboard, `docker build .` must work on linux/amd64 and build reasonably fast.
- Any language, framework and model. Model keys come from env vars, are named in the README, and are handed over at submission.
- LLM may learn, check and repair a portal; regular reads ideally run without a model call.
- No steering during the run. Max 1 request per second per portal. No logins, no captcha solving.
- Portals behind login or captcha are `failed` with a `reason`. Some portals show a captcha but still serve documents without it.
- While `healing`, keep serving the last correct tenders.
- Documents: unzip ZIPs; files over 250 MB may be skipped; each file has `fileName`, `sizeBytes`, `sha256`, `path`.
- Times are ISO 8601 with offset. Deadlines are shown in the portal's local time.
- Deliverables: Git repo with a Dockerfile at its root, a one-page README (how it works, env vars, cost per portal), a video of at most 3 minutes.

## 4. Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| A | **Recipe compiler:** LLM reads HTML, writes selectors, code runs them; rewrite on failure. | Fast to build; fails on JS apps, summary pages and multi-step downloads; identical to most teams. | Rejected |
| B | **Platform library:** fingerprint the portal software (footer, URL path, meta tags) and use a hand-written adapter. | Very reliable on real portals and a strong scaling argument (one adapter covers hundreds of portals); scores zero on the jury's custom portals. | Used as an accelerator |
| C | **Network-first explorer with self-verification:** headless browser records network traffic and prefers JSON APIs; Jev classifies pages and ranks links cheaply; the LLM writes a declarative recipe; the system verifies its own output field by field before serving. | Robust on unseen portals, cheap per portal, every decision traceable; more work, larger image (Playwright). | **Chosen as the core** |

**Picked by the team:** **C as the core, B as a turbo, plus the wow package** (Chaos Lab, Agent Theater, Jev decision visuals, ElevenLabs briefing).

## 5. Architecture

```
                    ┌──────────────── container :8080 ────────────────┐
 POST /portals ───▶ │ API (FastAPI)  ──▶  Job queue (asyncio workers) │
 GET  /…        ◀── │      │                    │                     │
 GET  /  (dash) ◀── │  SQLite + file store      ▼                     │
                    │      ▲          ┌─── Pipeline per portal ───┐   │
                    │      │          │ 1 Fingerprint (B)         │   │
                    │      │          │ 2 Explore  (Playwright+Jev)│  │
                    │      └──────────│ 3 Induce recipe (LLM)     │   │
                    │                 │ 4 Verify   (Jev + rules)  │   │
                    │                 │ 5 Serve    (no LLM)       │   │
                    │                 │ 6 Watch & Heal            │   │
                    │                 └───────────────────────────┘   │
                    │  Event bus ──▶ SSE ──▶ Agent Theater (dashboard)│
                    └─────────────────────────────────────────────────┘
```

**Stack (builder's call, stated):** Python 3.12, FastAPI + uvicorn, SQLite (WAL), asyncio background workers, `httpx` for plain HTTP, Playwright (Chromium) only when a page needs JavaScript, `selectolax`/`lxml` for parsing, `zoneinfo` for local times. Base image: official Playwright Python image pinned to a version, built with `--platform linux/amd64`.

### 5.1 The pipeline

**1 · Fingerprint (B).** Fetch the start URL, check URL path patterns, footer text, `<meta name="generator">` and script names against a small signature table of known portal software (German Vergabe platforms first, then FR/ES/NL). On a match, use the adapter's recipe template and skip straight to Verify. Target: 3–5 adapters, chosen from what our real-portal test set actually contains.

**2 · Explore (C).**
- Load the page in Playwright and **record all XHR/fetch responses**. If a JSON response looks like a tender list, the recipe will target that API (more stable than HTML).
- **Resolve summary pages:** links from TED or national sites often land on a notice summary, not the portal. Jev judges the page type; if it is a summary, follow the link it ranks highest as "goes to the tender documents / platform".
- **Jev page-type Choice** on every visited page: `tender_list`, `tender_detail`, `notice_summary`, `search_form`, `login_wall`, `captcha`, `error`, `other`.
- **Jev link ranking:** candidate links (found by code) are scored for "leads to the list of open tenders", "next page", "leads to this tender's detail page", "downloads documents". Code follows the top candidates within the 1 req/s budget.
- Every decision becomes an event (page, screenshot, question, probabilities) for the Agent Theater.

**3 · Induce recipe (LLM).** Given one list page, two or three detail pages and any captured JSON samples, the frontier LLM writes a **declarative recipe** (JSON) in our own small DSL, not free code:
- source: `html` or `json_api` (URL template, params, pagination: page param / offset / next-link / cursor)
- list item locator and the detail-link field
- field mappings for every tender field (CSS/XPath or JSON path, plus a normaliser: date, datetime+tz, CPV, country code, enum)
- document chain: one or more steps (detail page → documents tab → download links / ZIP), and whether JS is needed per step
- tender identity: which field is the portal's stable id

A deterministic engine executes recipes. The DSL keeps reads model-free, makes repairs a small diff, and lets the dashboard show exactly what the system "learned".

**4 · Verify (Jev + rules).** Before a portal becomes `serving`:
- Schema and rule checks: required fields present, dates parse, deadline after publication date, CPV is 8 digits, status rules.
- **Select, don't generate.** For dates, deadlines, reference numbers and buyer names, code extracts candidate spans with regex from the detail page; Jev picks the right candidate (Choice with a "none" option). Values are copied verbatim, so the model cannot invent them.
- **Field cross-check:** for a sample of tenders, one batched Jev request asks a Noul per field: "is `value` the `<field>` of the tender in this page text?". Fields below a threshold become `null` (or `null` + omitted for optional fields, per the brief).
- A recipe is accepted only if the sample passes; otherwise loop back to Induce with the failures (max N attempts, then `failed` with a reason).

**5 · Serve (no LLM).** Scheduled and on-demand reads (`POST /refresh`) run the recipe only. Tenders are upserted by portal id; `lastChangedAt` changes only when content changes; tenders missing from the list or past their deadline become `closed`; cancelled ones `withdrawn` when the portal says so. Documents are streamed to disk, hashed (SHA256), ZIPs unpacked (streaming; skip > 250 MB), and served from `/files/{id}`.

**6 · Watch & Heal.** After every read, a health check runs:
- hard signals: HTTP errors, zero items, recipe selectors that match nothing, schema failures
- soft signals: tender count dropped sharply, required fields suddenly `null`, a cheap Jev spot-check of one tender failing
- block detection: Jev says `login_wall` / `captcha` → try documents directly first (captcha-but-downloadable trap), otherwise `failed` with a reason

On a failure the portal goes `healing` (keeps serving the last correct tenders), a `repair` run starts, and the LLM receives the **old recipe, the stored snapshot of the last good page and the new page**, and patches only what changed. The patch goes through Verify. Success → `serving`; repeated failure → `failed` with a plain-language reason.

### 5.2 State machine

`integrating` → (verify ok) → `serving` → (health fails) → `healing` → (repair ok) → `serving`
`integrating`/`healing` → (block or N failed attempts) → `failed` (with `reason`). `failed` portals are retried on a slow backoff, so a temporarily down portal can recover.

### 5.3 API contract (copied from the brief's appendix; full brief in `docs/brief/Patterno-Track-Description.pdf`)

Every answer is JSON except the dashboard and document bytes. Lists are JSON arrays. No authentication. Unknown id → `404`. Every time is date-time with offset, e.g. `2026-12-09T10:00:00+01:00`. The brief defines no error body; we return `{"error": "<message>"}` (our call).

| Call | Answer |
|---|---|
| `POST /portals` body `{"url": "…"}` | `201` + `{"id": "…"}` at once, attach in the background. Same URL twice → same `id`. |
| `GET /portals` | `200`, list of `{id, url, state, reason, tenderCount, lastSuccessAt \| null}`. `state` ∈ `integrating`, `serving`, `healing`, `failed`; `reason` in words. |
| `GET /portals/{id}` | `200`, one entry as above. |
| `DELETE /portals/{id}` | `204`. System stops calling the portal within a minute. |
| `POST /portals/{id}/refresh` | `202` + `{"runId": "…"}` at once. If a read is already running, return its `runId`. Jury may call this every few minutes. |
| `GET /portals/{id}/tenders` | `200`, list of tenders. `?changedSince=<datetime>` → only tenders whose `lastChangedAt` is later. |
| `GET /portals/{id}/runs` | `200`, list of `{id, kind: read\|repair, startedAt, finishedAt \| null, result: ok\|failed\|null}` (`null` while running). The first attach is a `read`. |
| `GET /` | Dashboard (HTML). |
| `GET /files/{id}` | The document's raw bytes (our call: correct `Content-Type` guessed from the file name, `Content-Disposition` with `fileName`). |

Rules: while `healing`, keep serving the last correct tenders. A portal that blocks with login or captcha is `failed`, and `reason` says so.

**Tender fields**

| Field | | Meaning |
|---|---|---|
| `id` | required | The portal's own identifier; stable across reads |
| `url` | required | Address of the tender's page |
| `title` | required | As the portal states it |
| `buyerName` | required | The public body that buys |
| `publicationDate` | required | Date, e.g. `2026-11-20` |
| `submissionDeadline` | required | Date-time with offset |
| `status` | required | `open`; `closed` once the deadline passed or the portal no longer lists it; `withdrawn` when the buyer cancelled |
| `documents` | required | List of `{fileName, sizeBytes, sha256, path}`; `path` is our API path, e.g. `/files/ab12`. Unpack ZIPs. Files over 250 MB (ZIPs included) may be left out |
| `lastChangedAt` | required | When our system last saw this tender change |
| `referenceNumber` | if shown | Buyer's file number |
| `description` | if shown | What is bought |
| `procedureType` | if shown | `open`, `restricted`, `negotiated`, `other` |
| `contractNature` | if shown | `works`, `services`, `supplies` |
| `cpvCodes` | if shown | List of 8-digit strings, no check digit (`45000000`, not `45000000-7`) |
| `placeOfPerformance` | if shown | `{postalCode, city, countryCode}` (two letters, e.g. `DE`) |

Send `null` for a required field the portal does not show; leave optional fields out entirely. A wrong value is worse than a missing one.

A contract test suite checks every response shape against this table.

### 5.4 Calls we made where the brief is silent

- **Progressive delivery.** A portal turns `serving` as soon as the recipe is verified and the open-tender list is read; documents download in the background and appear as they finish. Large portals keep paging in the background at 1 req/s; `tenderCount` grows. Rationale: the jury sees correct data early, and a half-finished document download never blocks Attach.
- **Watch cadence.** Our own scheduler reads every portal every 10 minutes (staggered), and every jury `refresh` also runs the health check, so a mutation is noticed on the next read either way.
- **Persistence.** All state (SQLite + files) lives under `/data`, declared as a `VOLUME`. It works without a mount; with a mount it survives restarts. On start, portals in the DB resume from their stored recipes, no re-learning.
- **Jev access.** The team has Jev keys. The docs confirm batched questions in one request (run in parallel, "barely changes the response time"), Choice with up to 255 options (we add an explicit `none`), Noul as P(yes), Score, and image input (experimental). Python SDK `typesafe-sdk`, env var `TYPESAFE_API_KEY`. Rate limits are per organisation and shown in the console: check them at hour 0. If Jev is unavailable, the same questions fall back to the LLM.

## 6. The wow package

**6.1 Chaos Lab.** A separate small app (also shipped in the container under `/lab`, off by default for the jury) that generates fake portals the way the jury will:
- **Kinds:** server-rendered list, JS single-page app with a JSON API, multi-step document download, ZIP documents, login wall, captcha page with downloadable documents.
- **Mutations:** renamed classes/ids, reshuffled layout, switched pagination style, changed date format, moved API path / renamed JSON keys, HTML → JSON switch, a tender withdrawn, a deadline passed, the site down.
- **Ground truth + scorer:** every fake portal knows its true tenders; a scorer compares our API's output with the truth and prints Attach/Notice/Repair scores. This becomes our internal leaderboard during the 24 h.

**6.2 Agent Theater (dashboard).**
- **Morning view:** a grid of portals with state colours, tender count, last success, what broke and what the system did (from `/runs`).
- **Theater view per portal:** a live timeline over SSE: screenshot of the page the agent is on, the Jev question and its probability bars ("tender_detail 94 %"), the link it chose, the recipe as it is written, and the verify results with a per-field confidence heatmap.
- **Cost meter:** live cents per portal, split into Jev and LLM, with the projected cost for 4,500 portals.
- **"Break it" button** (lab portals only): picks a mutation, applies it, and the audience watches `serving → healing → serving`.

**6.3 Jev showcase.** Jev is the cheap, fast "eyes" of the system and the demo shows that directly:
- **Decision cards:** every Jev judgment shown with its typed answer and probabilities.
- **Jev Vision (visual only):** Jev's image input and Box primitive locate the tender table / documents link on a screenshot, drawn as an overlay in the Theater. The docs mark images as experimental, so this is **never on the critical path**; the pipeline works from text.
- **Race panel:** the same page classification by Jev and by the frontier LLM side by side, showing time and cost (docs report ~0.24 s and ~$0.0005 for 13 questions over a ~54k-char page in one batched call).

**6.4 ElevenLabs morning briefing.** A button on the dashboard generates a 20–30 s spoken summary of the night (template-filled from portal state and runs, optionally polished by the LLM), e.g. "Good morning. 14 portals are delivering. Two broke overnight and were repaired. One needs you: it now asks for a login." Also used as the video narration.

## 7. Team split (4 people, Claude assisting everyone)

| Person | Owns |
|---|---|
| P1 · Core | API contract, SQLite models, job queue, state machine, recipe engine, documents (download, hash, unzip, serve), Dockerfile |
| P2 · Brain | Explore (Playwright + network capture), Jev judgments, recipe induction prompt, Verify, Heal |
| P3 · Lab | Real-portal test set (≥4 countries, ≥3 software packages, 1–2 blocked), Chaos Lab generator + mutations + scorer, fingerprint adapters for what the test set shows |
| P4 · Show | Dashboard, Agent Theater, cost meter, Break-it, ElevenLabs briefing, README, video |

## 8. Timeline (24 h)

| Hours | Milestone |
|---|---|
| 0–2 | Repo, skeleton container with all endpoints returning valid shapes, contract tests, test-set list started, first Chaos Lab portal |
| 2–8 | End-to-end on one server-rendered lab portal and one real portal: attach → tenders + documents → serving |
| 8–14 | JSON-API path, multi-step documents, Verify with Jev, health checks, Heal loop; Chaos Lab mutations + scorer |
| 14–18 | Harden on the full test set (scorer drives the work); adapters; Agent Theater + cost meter |
| 18–21 | Wow polish: Break-it, Jev Vision overlay, race panel, ElevenLabs briefing; amd64 build test |
| 21–24 | Freeze features. README with cost per portal, video (max 3 min), final run on held-out portals, submit |

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Nobody has used Playwright before | Use it only where needed; plain `httpx` first; Claude writes the browser layer early so it is tested longest |
| Image too big / slow to build | Pinned official Playwright base image (pull, not compile); Chromium only; no extra browsers |
| No amd64 machine | `docker buildx build --platform linux/amd64` on the Mac; test-run once under emulation; ideally one run on a cheap cloud VM |
| Jev or LLM API down / rate-limited during judging | Retries with backoff; Jev falls back to the LLM for the same question; reads never depend on a model |
| Jev image input is experimental | Used only for the visual overlay, never for decisions |
| Wrong values cost more than missing ones | Select-don't-generate + per-field verification + `null` when unsure |
| Over-building the wow and under-building the core | Hard order: contract → attach → notice → repair → wow. Feature freeze at hour 21 |
| Jury mutates portals in ways we did not foresee | Network-first + snapshot diff repair is generic; Chaos Lab covers the common classes |

## 10. Left to the builder

- Exact recipe DSL schema, thresholds for Jev nouls (tune on Chaos Lab data), number of repair attempts, refresh schedule.
- Which 3–5 platform adapters to write (decided by the real test set).
- Dashboard visual style (must be "fancy" and readable on a projector).
- Prompt wording for recipe induction and repair.

## 11. Open for the team

- Which frontier LLM provider and model we use for Induce and Heal (env var name in the README).
