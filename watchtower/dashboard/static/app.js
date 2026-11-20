/* Watchtower dashboard: one EventSource, cards re-rendered from server fragments. No build step. */
const Watchtower = (() => {
  const $ = (s, el = document) => el.querySelector(s);
  const pending = new Map();

  async function api(method, url, body) {
    const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {},
                                 body: body ? JSON.stringify(body) : undefined });
    if (!r.ok && r.status !== 204) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.status === 204 ? null : r.json().catch(() => null);
  }

  /* Inline feedback (status / completion / error) instead of blocking alert() dialogs. */
  function toast(msg, kind = "ok") {
    document.querySelector(".toast")?.remove();
    const t = document.createElement("div"); t.className = `toast ${kind}`; t.setAttribute("role", kind === "error" ? "alert" : "status");
    t.textContent = msg; document.body.append(t); setTimeout(() => t.remove(), kind === "error" ? 6000 : 3000);
  }

  async function refreshCard(id) {
    const r = await fetch(`/ui/portals/${id}/card`);
    const old = document.getElementById(`card-${id}`);
    if (r.status === 404) { old?.remove(); return; }
    const tpl = document.createElement("template");
    tpl.innerHTML = (await r.text()).trim();
    const card = tpl.content.firstElementChild;
    if (old) old.replaceWith(card); else { $("#empty")?.remove(); $("#grid").prepend(card); }
    card.classList.add("flash"); setTimeout(() => card.classList.remove("flash"), 500);
    recount();
  }

  function scheduleCard(id) {
    clearTimeout(pending.get(id));
    pending.set(id, setTimeout(() => refreshCard(id), 250));
  }

  function recount() {
    for (const s of ["serving", "healing", "integrating", "failed"]) {
      const el = $(`#n-${s}`); if (el) el.textContent = document.querySelectorAll(`.card.state-${s}`).length;
    }
    scheduleSummary();
  }

  let summaryTimer = null;
  async function refreshSummary() {
    try {
      const s = await api("GET", "/ui/summary");
      if (!s) return;
      for (const k of ["serving", "healing", "integrating", "failed"]) {
        const el = $(`#n-${k}`); if (el) el.textContent = s[k];
      }
      if ($("#n-tenders")) $("#n-tenders").textContent = s.tenders;
      if ($("#n-avg-usd")) $("#n-avg-usd").textContent = `$${Number(s.avg_usd || 0).toFixed(3)}`;
      if ($("#n-projected")) $("#n-projected").textContent = `$${Math.round(s.projected || 0)}`;
    } catch {}
  }

  function scheduleSummary() {
    clearTimeout(summaryTimer);
    summaryTimer = setTimeout(refreshSummary, 250);
  }

  function describe(ev) {
    const d = ev.data || {};
    switch (ev.kind) {
      case "state": return `→ ${d.to}${d.reason ? " · " + d.reason : ""}`;
      case "log": return d.msg;
      case "judgment": return `Jev: ${d.question?.slice(0, 60) || ""} → ${d.answer}`;
      case "recipe": return d.reason === "repaired" ? "Recipe patched" : "Recipe written";
      case "verify": return `Verified sample: ${d.ok ? "ok" : "rejected"}`;
      case "page": return `${d.via || "http"} ${d.status} ${String(d.url || "").slice(0, 60)}`;
      default: return null;
    }
  }

  function feed(ev) {
    const text = describe(ev); const list = $("#feed");
    if (!text || !list || ev.kind === "page") return;
    const li = document.createElement("li");
    const host = document.querySelector(`#card-${ev.portal_id} .host`)?.textContent || "system";
    li.innerHTML = `<b></b><span></span>`;
    li.querySelector("b").textContent = host; li.querySelector("span").textContent = text;
    list.prepend(li);
    while (list.children.length > 12) list.lastChild.remove();
  }

  function connect(onEvent) {
    const es = new EventSource("/events");
    es.onmessage = (m) => onEvent(JSON.parse(m.data));
    for (const k of ["state", "run", "log", "judgment", "recipe", "verify", "page", "explore", "cost", "llm"])
      es.addEventListener(k, (m) => onEvent(JSON.parse(m.data)));
    return es;
  }

  function morning() {
    $("#add-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = e.target.url, btn = e.target.querySelector("button");
      btn.disabled = true;
      try { const { id } = await api("POST", "/portals", { url: input.value.trim() }); input.value = ""; refreshCard(id); }
      catch (err) { toast(err.message, "error"); } finally { btn.disabled = false; }
    });
    document.addEventListener("click", async (e) => {
      const t = e.target;
      if (t.dataset.refresh) {
        t.disabled = true;
        try { await api("POST", `/portals/${t.dataset.refresh}/refresh`); toast("Reading the portal now"); }
        catch (err) { toast(err.message, "error"); }
        finally { t.disabled = false; }
        scheduleCard(t.dataset.refresh);
      }
      if (t.dataset.break) { t.disabled = true; const r = await api("POST", `/portals/${t.dataset.break}/break`).catch((e) => toast(e.message, "error")); if (r) toast(`Chaos Lab broke the portal: ${r.mutation}. Watch it heal.`); if (r) t.textContent = `Broke: ${r.mutation}`; setTimeout(() => { t.disabled = false; t.textContent = "Break it"; }, 4000); }
      if (t.dataset.delete && confirm("Remove this portal and its tenders?")) {
        await api("DELETE", `/portals/${t.dataset.delete}`).then(() => toast("Portal removed")).catch((e) => toast(e.message, "error")); document.getElementById(`card-${t.dataset.delete}`)?.remove(); recount();
      }
    });
    $("#brief-btn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget, out = $("#brief-text"), audio = $("#brief-audio");
      btn.disabled = true; btn.textContent = "Preparing…";
      try {
        out.textContent = await (await fetch("/briefing.txt")).text(); out.hidden = false;
        const r = await fetch("/briefing.mp3");
        if (r.ok) { audio.src = URL.createObjectURL(await r.blob()); audio.hidden = false; audio.controls = true; await audio.play(); }
      } catch (err) { toast(err.message, "error"); }
      finally { btn.disabled = false; btn.textContent = "▶ Morning briefing"; }
    });
    connect((ev) => {
      if (ev.portal_id && ["state", "run"].includes(ev.kind)) scheduleCard(ev.portal_id);
      if (ev.kind === "cost") scheduleSummary();
      feed(ev);
    });
    setInterval(() => document.querySelectorAll(".card").forEach((c) => scheduleCard(c.dataset.id)), 60000);
  }

  /* Model calls panel: recent LLM and Jev calls as MLflow recorded them. */
  function traces() {
    const box = $("#traces"); if (!box) return;
    const seen = new Set(); let timer = null;
    const money = (v) => v == null ? "–" : v >= 0.01 ? "$" + v.toFixed(3) : "$" + v.toFixed(6);
    const ago = (ms) => { const s = Math.max(0, (Date.now() - ms) / 1000);
      return s < 60 ? Math.round(s) + " s ago" : s < 3600 ? Math.round(s / 60) + " min ago" : Math.round(s / 3600) + " h ago"; };
    function message(body, text) {
      body.textContent = ""; const tr = document.createElement("tr"), td = document.createElement("td");
      td.colSpan = 7; td.className = "muted"; td.textContent = text; tr.append(td); body.append(tr);
    }
    async function load() {
      const body = $("#t-rows"); let data;
      try { data = await (await fetch("/traces")).json(); } catch { message(body, "Could not reach the trace store."); return; }
      const rows = data.traces || [];
      for (const k of ["llm", "jev"]) {
        const rs = rows.filter((r) => r.kind === k);
        $("#t-" + k + "-n").textContent = rs.length;
        $("#t-" + k + "-usd").textContent = money(rs.reduce((a, r) => a + (r.usd || 0), 0));
      }
      if (!rows.length) { message(body, "No model calls yet. Add a portal or press Break it."); return; }
      body.textContent = "";
      for (const r of rows) {
        const tr = document.createElement("tr"); if (seen.size && !seen.has(r.id)) tr.className = "new";
        const host = document.querySelector("#card-" + r.portal_id + " .host")?.textContent || r.portal_id || "–";
        const what = r.kind === "llm" ? (r.purpose || "") + " · " + (r.model || "") : (r.purpose || "");
        const cells = [ago(r.at), "", host, what, (r.tokens_in ?? "–") + " / " + (r.tokens_out ?? "–"),
                       r.ms != null ? (r.ms / 1000).toFixed(1) + " s" : "–", money(r.usd)];
        cells.forEach((c, i) => {
          const td = document.createElement("td");
          if (i === 1) { const k = document.createElement("span"); k.className = "k k-" + r.kind; k.textContent = r.kind === "llm" ? "LLM" : "Jev"; td.append(k); }
          else if (i === 3) { const w = document.createElement("span"); w.textContent = c; td.title = c; td.append(w); }
          else td.textContent = c;
          if (i === 3) td.className = "what"; if (i >= 4) td.className = "r";
          tr.append(td);
        });
        body.append(tr);
      }
      rows.forEach((r) => seen.add(r.id));
    }
    const soon = () => { clearTimeout(timer); timer = setTimeout(load, 2500); };  // MLflow writes traces in the background
    load(); setInterval(load, 30000);
    connect((ev) => { if (["llm", "judgment"].includes(ev.kind)) soon(); });
  }

  return { api, connect, morning, refreshCard, toast, traces, $ };
})();
