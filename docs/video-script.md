# Watchtower: demo video script (about 2:50)

**Format:** a short hook on camera, then a screen recording with your voice.
**Label:** the replay as "Replay", the live part as "Live", any cut wait as "⏩ time compressed".

## Before recording

1. Run `scripts/demo_reset.sh http://localhost:8090`. Wait until the real portals show **delivering**.
2. Press **⚡ Run demo: one night (replay)** once and let it finish. That seeds the lab portals the live break uses. Then reload the page.
3. Browser: only `localhost:8090` open, zoom 90 %, bookmarks bar hidden, Do Not Disturb on.
4. Record with `Cmd+Shift+5` and your microphone, one clip per scene.

Timing: the tour has no waits. The replay runs about 30 s. The live break goes from click to green in about 20 s (measured: 21 s).

---

## 0:00–0:12 · Hook (on camera, or a black screen with text)

**On-screen text:** `POV: it's 3 a.m. and a procurement portal just redesigned its website.`

> "Every night, some of Patterno's 4,500 portals change without warning. A scraper breaks, and nobody notices for days. Meanwhile customers miss tenders and reports are built on stale data."
>
> "So we built Watchtower. Let's do a value check."

## 0:12–0:50 · A short tour of the site

| Click | Say |
|---|---|
| **Overview**, scroll to **Monitored portals** | "This is what a Patterno analyst sees in the morning. One card per portal. Green means it delivers, amber means it is repairing itself, red means it needs a person, always with the reason." |
| Point at one real card (e.g. TenderNed): tenders, last success, model cost | "Each card shows how many tenders it serves, when it last succeeded, and what it cost in model calls. Known portal software costs a tenth of a cent." |
| **Tenders** (left), search `TenderNed` or pick a vmstart tender, click it, open a document | "Every tender comes with its details and its documents, as real files, not links to a login page." |
| Back to **Overview**, point at the cost tile | "Here's what all this costs, projected to 4,500 portals." |
| Optional: `localhost:8090/docs` | "And everything is an API, so Patterno's pipeline can use it directly." |

## 0:50–1:35 · One night, replayed (label: "Replay")

**Click:** **⚡ Run demo: one night (replay)**. Then just talk. Don't click anything until the purple morning line appears (about 30 s).

What happens on screen, in order:
- **0:00–0:08 (replay clock):** a new card **spa** appears with *learning…* and turns green: *delivering*.
- **0:12:** three portals turn amber at the same time.
- **0:15–0:25:** they turn green one by one; the counters climb.
- **0:26–0:28:** one portal ends red: *needs a person* (documents behind a login).
- **0:30:** purple morning summary.

> "All Watchtower gets is a URL. It explores the portal once, an LLM writes a recipe, and Jev, a small and cheap model, checks ten tenders against the real pages before anything is served. From then on every read is plain code, with no LLM."
>
> "Now the night. Three portals change their layout at once. Nobody presses refresh: a pulse notices it by itself, at zero model cost. While a portal is broken, customers keep getting the last verified tenders. Watchtower repairs itself for a few cents each."
>
> "And this one put its documents behind a login. Watchtower doesn't break in and doesn't guess. It says: this one needs a person, and here's why."

## 1:35–2:05 · Live: break one portal (label: "Live")

**Click:** **💥 Break a portal live** (top of the Overview, next to ⚡). The page scrolls to the **lab/ssr** card and outlines it. Don't click anything else.
- After about 2 s the card turns amber: *Healing*, with the reason.
- After about 20 s it is green again: *serving*, *Repaired itself*.

> "That was a replay. This is live. I just changed this portal's layout, the way a real site redesign would. I don't press refresh."
>
> "The pulse noticed it: the tender list no longer matches. The tenders stay served. Now a real model call patches the recipe, and it's checked before it goes live… and it's back."

## 2:05–2:25 · Proof of the repair

**Click** on the same card: **Before vs after**. Then scroll down to **Model calls**.

> "Every repair can be checked: the old page and the new one, and exactly how the recipe followed. And here is every model call with what it cost."

## 2:25–2:50 · The numbers (honest)

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
- If **Break a portal live** says there is no portal to break, run the replay once first.
- If the live repair takes longer than 30 s, cut the wait and label it "⏩ time compressed".
- Only say numbers that are shown on screen or written in the README.
