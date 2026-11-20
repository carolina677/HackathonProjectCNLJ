# Demo video (≤ 3:00)

## Before recording (5 minutes)

1. Start the server with the lab and a fast pulse: `WATCHTOWER_LAB=1 PULSE_INTERVAL_SECONDS=60`, with real keys in `.env`.
   - Docker: `docker run -p 8080:8080 --env-file .env -e WATCHTOWER_LAB=1 -e PULSE_INTERVAL_SECONDS=60 watchtower`
2. Run `scripts/demo_reset.sh http://localhost:8080`. It attaches 3 real portals (DE NetServer, FR Atexo, NL TenderNed) and 2 lab portals.
3. Wait until the real portals are green (`serving`). Open http://localhost:8080 at browser zoom 90 % and hide the bookmarks bar.
4. Set up the voice-over: record it separately (ElevenLabs or your own voice) and lay it over the footage afterwards. Don't talk while clicking.

## Shot list

| Time | Screen | Narration |
|---|---|---|
| 0:00–0:15 | Dashboard, real portals green. Slowly hover over the cards. | "Patterno reads tenders from 4,500 public procurement portals. Every portal is built differently, and they change without warning. Today, when one breaks, nobody notices for days." |
| 0:15–0:45 | Type a portal URL into **Add portal**. The card says *Learning the portal…*. Open **Theater**: the agent's steps, then the Jev checks. | "We give Watchtower nothing but a URL. Known portal software is recognised at once. An unknown portal is explored by the LLM once: it writes a recipe, and Jev checks ten tenders against the page before anything is served. Documents come along, ZIPs unpacked." |
| 0:45–1:45 | Press **⚡ Run demo**. The demo stage opens at the top of the page and nobody scrolls. Three portals turn amber at the same time, then green one after another. The value counters climb. One portal ends red: *documents behind a login*. | "Now it's night, and three portals change their layout at the same time. Nobody presses refresh: the 15-minute pulse notices it by itself, at zero model cost. Customers keep getting every tender while the portals are broken. One after another, Watchtower repairs itself, for a few cents each. And when a portal locks its documents behind a login, it doesn't guess. It says so: this one needs a person." |
| 1:45–2:15 | Click a repaired portal → **Theater** → **Before vs after**. Point at the recipe diff. | "Every repair can be checked: here is exactly what changed on the page, and exactly what the system changed in its recipe." |
| 2:15–2:35 | Press **💥 Break 3 portals live**. Within a minute the cards turn amber, then green: real repairs, real model calls. Show the **Model calls** panel. | "This was not a recording: these are real repairs with real model calls. Reading a portal costs no LLM at all. Here you see every call and what it cost." |
| 2:35–2:50 | Value counters and *Avg. cost / portal* with the 4,500 projection. | "That's what makes 4,500 portals affordable: [measured number] per portal, plus a few cents per repair." |
| 2:50–3:00 | Press **▶ Morning briefing**, let it play for 5 s, end on the dashboard. | "Watchtower. Attach, notice, repair, every morning." |

## Rules for the recording

- **Don't promise anything the screen doesn't show.** If there is no measured cost number yet, say "a few cents per repair" and no more.
- **⚡ Run demo is a replay of the real flow; label it so.** The proof is the live part with **💥 Break 3 portals live**. Show it, even if it takes 60 s; cut the waiting time in the edit.
- **If something goes wrong live:** `scripts/demo_reset.sh`, then wait 2 minutes and start the shot again.
