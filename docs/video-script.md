# Watchtower: demo video script (about 2:45)

**Format:** a short hook on camera, then a screen recording with your voice.
**Label:** the replay as "Replay", the live part as "Live", any cut wait as "⏩ time compressed".

## Before recording

1. Run `scripts/demo_reset.sh http://localhost:8090`. Wait until the portals show **delivering**.
2. Browser: only `localhost:8090` open, zoom 90 %, bookmarks bar hidden, Do Not Disturb on.
3. Record with `Cmd+Shift+5` and your microphone, one clip per scene.
4. **Nothing in the main path waits on a live model call.** The replay runs about 30 seconds from start to the morning summary.

---

## 0:00–0:12 · Hook (on camera, or a black screen with text)

**On-screen text:** `POV: it's 3 a.m. and a procurement portal just redesigned its website.`

> "Every night, some of Patterno's 4,500 portals change without warning. A scraper breaks, and nobody notices for days. Meanwhile customers miss tenders and reports are built on stale data."
>
> "So we built Watchtower. Let's do a value check."

## 0:12–1:35 · One night, replayed (label: "Replay")

**Click:** **⚡ Run demo: one night (replay)** on the Overview page. Then just talk; don't click anything until the purple morning line appears (about 30 s).

What happens on screen, in order:
- **0:00–0:08 (replay clock):** a new card **spa** appears with *learning…*. The story says: only a URL; Jev sees a JavaScript tender list; one LLM call writes a recipe ($0.025); Jev checks 10 tenders. The card turns green: *delivering*.
- **0:12–0:14:** three portals turn amber at the same time.
- **0:15–0:25:** they turn green one by one; the counters climb.
- **0:26–0:28:** one portal ends red: *needs a person* (documents behind a login).
- **0:30:** purple morning summary: 3 repaired, 92 tenders stayed served, cost tonight $0.19.

> "All Watchtower gets is a URL. It explores the portal once, an LLM writes a recipe, and Jev, a small and cheap model, checks ten tenders against the real pages before anything is served. From then on every read is plain code, with no LLM."
>
> "Now the night. Three portals change their layout at once. Nobody presses refresh: a 15-minute pulse notices it by itself, at zero model cost. While a portal is broken, customers keep getting the last verified tenders. Watchtower repairs itself, one portal after another, for a few cents each."
>
> "And this one put its documents behind a login. Watchtower doesn't break in and doesn't guess. It says: this one needs a person, and here's why."

## 1:35–1:55 · Proof of a repair

**Screen:** open a repaired portal, then **Theater**, then **Before vs after**. Point at the old and new selector.

> "Every repair can be checked: what changed on the page, and exactly how the recipe followed."

## 1:55–2:25 · Live, real model calls (optional)

Skip this scene if you are short on time; the replay already shows the whole flow.

**Screen:**
1. Press **💥 Break 3 portals live**.
2. Stop the recording, wait until the cards turn green again (1–2 min), record again. Label the cut "⏩ time compressed".
3. Show the **Model calls** panel.

> "That was a replay of the real flow. This is live: three real breaks, real repairs, real model calls. You can see every call and what it cost."

## 2:25–2:45 · The numbers (honest)

**Screen:** the cost tile with the projection to 4,500 portals.

> "Here's what we measured:
> - Known portal software costs a tenth of a cent per portal.
> - An unseen portal costs about two and a half cents to learn.
> - On six portals generated blind at run time, Watchtower attached six out of six.
> - On real holdout portals it managed two out of six on the first run, and four out of six after general fixes.
> - The rest fail with a clear reason, never with wrong data."
>
> "Watchtower: it attaches, it notices, it repairs. Every morning."

**Final overlay:** `Measured: $0.025 per unseen portal · reads cost no LLM · Docker, linux/amd64 · API + dashboard on :8080`

---

### If something goes wrong

- Run `scripts/demo_reset.sh http://localhost:8090` and press **⚡ Run demo** again; the replay can run as often as you like.
- The live repair takes 1–2 minutes. Cut it, don't talk over the wait.
- Only say numbers that are shown on screen or written in the README.
