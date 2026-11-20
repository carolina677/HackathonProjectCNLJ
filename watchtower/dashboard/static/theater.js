/* Agent Theater: replays the portal's event history, then follows it live. */
const Theater = (() => {
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const pct = (p) => `${Math.round(p * 100)}%`;
  const fmtUsd = (u) => (u == null ? "–" : `$${u < 0.01 ? u.toFixed(5) : u.toFixed(3)}`);
  let pid, live = false;

  function line(ev, text, cls = "") {
    const li = el("li", cls); li.append(el("time", null, new Date(ev.ts).toLocaleTimeString()), el("span", null, text));
    $("#timeline").prepend(li);
    while ($("#timeline").children.length > 200) $("#timeline").lastChild.remove();
  }

  function shot(ev) {
    const d = ev.data;
    if (!d.screenshot) return;
    const box = $("#shot"); box.innerHTML = "";
    const img = el("img"); img.src = d.screenshot; img.alt = "Page the agent is on";
    box.append(img); $("#shot-url").textContent = d.url;
  }

  function vision(ev) {
    const box = $("#shot"); if (!box.querySelector("img")) return;
    box.querySelectorAll(".vbox").forEach((b) => b.remove());
    for (const b of ev.data.boxes) {
      const v = el("div", "vbox", `${b.label} ${pct(b.confidence)}`);
      Object.assign(v.style, { top: pct(b.top), left: pct(b.left), width: pct(b.right - b.left), height: pct(b.bottom - b.top) });
      box.append(v);
    }
  }

  function decision(ev) {
    const d = ev.data, card = el("div", "decision");
    const head = el("div", "dhead");
    head.append(el("span", `src src-${d.source}`, d.source), el("b", null, d.answer), el("small", null, d.ms != null ? `${d.ms} ms` : ""));
    card.append(head, el("p", "q", d.question));
    const probs = Object.entries(d.probabilities || {}).sort((a, b) => b[1] - a[1]).slice(0, 4);
    for (const [k, p] of probs) {
      const row = el("div", "bar" + (k === String(d.answer) ? " chosen" : ""));
      const fill = el("i"); fill.style.width = pct(p);
      row.append(el("span", null, k), fill, el("em", null, pct(p)));
      card.append(row);
    }
    $("#decisions").prepend(card);
    while ($("#decisions").children.length > 12) $("#decisions").lastChild.remove();
  }

  function recipe(ev) {
    $("#recipe").textContent = JSON.stringify(ev.data.recipe, null, 1);
    $("#recipe-why").textContent = ev.data.reason === "repaired" ? "patched after the portal changed" : "learned from the portal";
    const diff = $("#diff"); diff.innerHTML = "";
    for (const d of ev.data.diff || []) diff.append(el("li", null, d));
  }

  function verify(ev) {
    const heat = $("#heat"); heat.innerHTML = "";
    const scores = ev.data.field_scores || {};
    if (!Object.keys(scores).length) { heat.append(el("p", "muted", ev.data.judged === false ? "No judge available: rule checks only." : "No fields scored.")); }
    for (const [f, s] of Object.entries(scores)) {
      const c = el("div", "cell"); c.style.setProperty("--s", s);
      c.append(el("b", null, f), el("span", null, pct(s)));
      if ((ev.data.mask || []).includes(f)) c.append(el("small", null, "→ null"));
      heat.append(c);
    }
    heat.append(el("p", ev.data.ok ? "ok" : "bad", ev.data.ok ? "Sample verified" : "Sample rejected — trying again with feedback"));
  }

  function state(ev) {
    const d = ev.data;
    $("#banner").className = `banner state-${d.to}`; $("#state").textContent = d.to;
    const r = $("#reason"); r.hidden = !d.reason; r.textContent = d.reason || "";
  }

  function costEv(ev) {
    const d = ev.data;
    $("#c-jev").textContent = `${(d.jev_usd * 100).toFixed(3)}¢`;
    $("#c-llm").textContent = `${(d.llm_usd * 100).toFixed(3)}¢`;
  }

  function race(data) {
    const panel = $("#race"); panel.hidden = false;
    const box = panel.querySelector(".race"); box.innerHTML = "";
    const maxMs = Math.max(data.jev.ms || 0, data.llm.ms || 0, 1);
    for (const [name, r] of [["Jev", data.jev], ["Frontier LLM", data.llm]]) {
      const side = el("div", "side");
      side.append(el("h4", null, name), el("p", "ans", r.error ? `unavailable: ${r.error}` : r.answer));
      const bar = el("div", "bar"); const fill = el("i"); fill.style.width = `${((r.ms || 0) / maxMs) * 100}%`;
      bar.append(el("span", null, r.ms != null ? `${r.ms} ms` : "–"), fill, el("em", null, fmtUsd(r.usd)));
      side.append(bar); box.append(side);
    }
  }

  function handle(ev) {
    const d = ev.data || {};
    switch (ev.kind) {
      case "page": shot(ev); line(ev, `${d.via} ${d.status} ${d.url}`, "dim"); break;
      case "vision": vision(ev); line(ev, `Jev Vision: ${d.boxes.map((b) => b.label).join(", ")}`); break;
      case "judgment": decision(ev); line(ev, `${d.source}: ${d.answer} — ${String(d.question).slice(0, 90)}`); break;
      case "links": line(ev, `Ranked links for "${d.goal}": ${(d.top || []).slice(0, 2).map((l) => `${l.text || l.href} ${pct(l.p)}`).join(" · ")}`); break;
      case "explore": line(ev, `Explored ${d.path.length} pages${d.blocked ? ` — blocked by ${d.blocked}` : ""}${d.error ? ` — ${d.error}` : ""}`); break;
      case "recipe": recipe(ev); line(ev, d.reason === "repaired" ? `Recipe patched: ${(d.diff || []).length} changes` : "Recipe written", "hi"); break;
      case "verify": verify(ev); line(ev, `Verification ${d.ok ? "passed" : "failed"}`, d.ok ? "hi" : "warn"); break;
      case "state": state(ev); line(ev, `State → ${d.to}${d.reason ? ": " + d.reason : ""}`, "hi"); break;
      case "cost": costEv(ev); break;
      case "llm": line(ev, `LLM (${d.purpose}): ${d.tokens} tokens, ${fmtUsd(d.usd)}, ${d.ms} ms`); break;
      case "race": race(d); break;
      case "compare": if (live) loadRepairs(d.n); line(ev, d.ok ? `Before vs after #${d.n} ready: see the top of the page` : `Repair #${d.n} failed: before vs after shows why`, d.ok ? "hi" : "warn"); break;
      case "run": if (d.status === "finished") { line(ev, `Run ${d.result}${d.detail ? ": " + d.detail : ""}`); if (live) refreshCount(); } break;
      case "log": line(ev, d.msg); break;
    }
  }

  // --- Before vs after: the last repair, side by side -------------------------------------
  let repairs = [];

  function showRepair(r) {
    const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
    const cap = (side, label) => {
      const s = r[side];
      const reads = s.source === "json_api" ? "reads a JSON API (not drawn on the page)" : `reads ${plural(s.items, "tender row")}`;
      const what = side === "before" ? `${plural(s.changed, "page element")} changed or gone` : `${plural(s.changed, "page element")} new or changed`;
      return `${label} · ${side === "before" ? "old" : "new"} recipe ${reads} · ${what}`;
    };
    $("#cmp-title").textContent = r.ok
      ? `repair #${r.n}, ${new Date(r.at).toLocaleString()} · healed after ${plural(r.attempts, "attempt")}`
      : `repair #${r.n}, ${new Date(r.at).toLocaleString()} · could not repair: a person is needed`;
    $("#cmp-before-cap").textContent = cap("before", "Before");
    $("#cmp-after-cap").textContent = r.ok ? cap("after", "After") : "After · no working recipe yet";
    $("#cmp-before").src = `/portals/${pid}/repairs/${r.n}/before.html`;
    $("#cmp-after").src = `/portals/${pid}/repairs/${r.n}/after.html`;
    const diff = $("#cmp-diff"); diff.innerHTML = "";
    for (const d of (r.diff || []).slice(0, 12)) diff.append(el("li", null, d));
    if (!r.ok && r.reason) diff.append(el("li", null, r.reason));
    $("#compare").hidden = false;
  }

  async function loadRepairs(pick) {
    const r = await fetch(`/portals/${pid}/repairs`);
    if (!r.ok) return;
    repairs = await r.json();
    if (!repairs.length) return;
    const sel = $("#cmp-pick"); sel.innerHTML = "";
    for (const x of repairs) sel.append(new Option(`#${x.n} · ${new Date(x.at).toLocaleTimeString()} · ${x.ok ? "healed" : "failed"}`, x.n));
    sel.closest("label").hidden = repairs.length < 2;
    const chosen = repairs.find((x) => x.n === pick) || repairs[0];
    sel.value = chosen.n;
    showRepair(chosen);
  }

  async function refreshCount() {
    const p = await (await fetch(`/portals/${pid}`)).json();
    $("#count").textContent = p.tenderCount;
  }

  async function start(id) {
    pid = id;
    for (const ev of await (await fetch(`/portals/${pid}/timeline`)).json()) handle(ev);
    live = true;
    await loadRepairs();
    $("#cmp-pick").addEventListener("change", (e) => { const r = repairs.find((x) => x.n === Number(e.target.value)); if (r) showRepair(r); });
    const es = new EventSource(`/events?portal=${pid}`);
    const kinds = ["page", "vision", "judgment", "links", "explore", "recipe", "verify", "state", "cost", "llm", "race", "run", "log", "compare"];
    for (const k of kinds) es.addEventListener(k, (m) => { const ev = JSON.parse(m.data); if (ev.portal_id === pid) handle(ev); });
    $("#race-btn").addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Racing…";
      try { const r = await fetch(`/portals/${pid}/race`, { method: "POST" }); if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || `The race failed (HTTP ${r.status}).`); } }
      catch (err) { Watchtower.toast(err.message, "error"); }
      finally { e.target.disabled = false; e.target.textContent = "Race Jev vs LLM"; }
    });
    document.addEventListener("click", async (e) => {
      const t = e.target;
      if (t.dataset.refresh) { t.disabled = true; await fetch(`/portals/${pid}/refresh`, { method: "POST" }); Watchtower.toast("Reading the portal now"); setTimeout(() => (t.disabled = false), 1500); }
      if (t.dataset.break) { t.disabled = true; const r = await (await fetch(`/portals/${pid}/break`, { method: "POST" })).json(); Watchtower.toast(`Chaos Lab broke the portal: ${r.mutation}. Watch it heal.`); t.textContent = `Broke: ${r.mutation}`; setTimeout(() => { t.disabled = false; t.textContent = "Break it"; }, 4000); }
    });
  }

  return { start };
})();
