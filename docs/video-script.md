# Watchtower: demo video script (about 2:45)

**Format:** a short hook on camera, then a screen recording with your voice.
**Label every cut:**
- a cut wait as "⏩ time compressed"
- the replay as "Replay"
- the live part as "Live"

## Before recording

1. Run `scripts/demo_reset.sh http://localhost:8090`. Wait until the 3 real portals show **delivering**, which takes about 3 minutes.
2. Pick a random number for the blind portal, for example `http://localhost:8090/blind/58213/`. **Do not open it beforehand.**
3. Browser: only `localhost:8090` open, zoom 90 %, bookmarks bar hidden, Do Not Disturb on.
4. Record with `Cmd+Shift+5` and your microphone, one clip per scene.
5. Order matters: **⚡ Run demo** first, then **💥 Break 3 portals live**.

---

## 0:00–0:12 · Hook (on camera, or a black screen with text)

**On-screen text:** `POV: it's 3 a.m. and a procurement portal just redesigned its website.`

> "Every night, some of Patterno's 4,500 portals change without warning. A scraper breaks, and nobody notices for days. Meanwhile customers miss tenders and reports are built on stale data."
>
> "So we built Watchtower. Let's do a value check."

## 0:12–0:45 · Attach a portal nobody has seen

**Screen:**
1. Paste the blind URL into **Add portal**. The card shows *Learning the portal…*
2. Open **Theater**: the agent's steps, then the Jev checks.
3. Cut ("⏩ time compressed") to **delivering**. Open one tender and download a PDF.

> "All Watchtower gets is a URL. This portal was generated seconds ago: new layout, new language, new date format. Nobody has seen it, not even us."
>
> "The LLM explores it once and writes a recipe. Jev, a small and cheap model, checks ten tenders against the real pages before anything is served. If a value can't be verified, it stays empty instead of guessed."
>
> "Every tender, every document. And from now on, every read is plain code, with no LLM."

## 0:45–1:35 · One night in fast-forward (Replay)

**Screen:** press **⚡ Run demo: one night (replay)**. The stage opens at the top:
- three portals turn amber at the same time, then go green one by one;
- the counters climb;
- one portal ends at *needs a person*.

> "Now the night. Three portals change their layout at once. Nobody presses refresh: a 15-minute pulse notices it by itself, at zero model cost."
>
> "While a portal is broken, customers keep getting the last verified tenders. Watchtower repairs itself, one portal after another, for a few cents each."
>
> "And this one put its documents behind a login. Watchtower doesn't break in and doesn't guess. It says: this one needs a person, and here's why."

## 1:35–1:55 · Proof of a repair

**Screen:** open a repaired portal, then **Theater**, then **Before vs after**. Point at the old and new selector.

> "Every repair can be checked: what changed on the page, and exactly how the recipe followed."

## 1:55–2:25 · Live, real model calls

**Screen:**
1. Press **💥 Break 3 portals live**.
2. Cut ("⏩ time compressed") until the cards turn green again.
3. Show the **Model calls** panel.

> "That was a replay of the real flow. This is live: three real breaks, real repairs, real model calls. You can see every call and what it cost."

## 2:25–2:45 · The numbers (honest)

**Screen:** the cost tile with the projection to 4,500 portals.

> "Here's what we measured:
> - Known portal software costs a tenth of a cent per portal.
> - An unseen portal costs about two and a half cents to learn.
> - On six portals generated blind, Watchtower attached six out of six.
> - On real holdout portals it managed two out of six on the first run, and four out of six after general fixes.
> - The rest fail with a clear reason, never with wrong data."
>
> "Watchtower: it attaches, it notices, it repairs. Every morning."

**Final overlay:** `Measured: $0.025 per unseen portal · reads cost no LLM · Docker, linux/amd64 · API + dashboard on :8080`

---

### If something goes wrong

- Run `scripts/demo_reset.sh http://localhost:8090`, wait 3 minutes and record the scene again.
- The live repair takes 1–2 minutes. Cut it, don't talk over the wait.
- Only say numbers that are shown on screen or written in the README.
