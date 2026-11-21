/* Watchtower + Briefly unified UI: one EventSource, live portal cards, searchable tenders, PDF downloads & before/after diffs. */
const Watchtower = (() => {
  const $ = (s, el = document) => el.querySelector(s);
  const byId = (id) => document.getElementById(id);
  const pendingCards = new Map();

  const COUNTRY_LABEL = {
    DE: "Germany", FR: "France", NL: "Netherlands", IE: "Ireland", AT: "Austria",
    ES: "Spain", PL: "Poland", LU: "Luxembourg", IT: "Italy", BE: "Belgium",
    PT: "Portugal", SE: "Sweden", DK: "Denmark", FI: "Finland", CZ: "Czechia",
  };
  const STORAGE_KEY = "briefly-saved-tenders";
  const dateFmt = new Intl.DateTimeFormat(document.documentElement.lang || "en", { dateStyle: "medium", timeZone: "UTC" });
  const whenFmt = new Intl.DateTimeFormat(document.documentElement.lang || "en", { dateStyle: "medium", timeStyle: "short" });

  let saved = new Set();
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (Array.isArray(raw)) saved = new Set(raw.filter((x) => typeof x === "string"));
  } catch {}

  function syncSavedBadge() {
    const badge = byId("nav-saved-badge");
    if (!badge) return;
    badge.textContent = saved.size;
    badge.hidden = saved.size === 0;
  }

  async function api(method, url, body) {
    const r = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok && r.status !== 204) {
      const payload = await r.json().catch(() => ({}));
      const detail = typeof payload.detail === "string" ? payload.detail : "";
      throw new Error(payload.error || detail || r.statusText);
    }
    return r.status === 204 ? null : r.json().catch(() => null);
  }

  function toast(msg, kind = "ok") {
    document.querySelector(".toast")?.remove();
    const t = document.createElement("div");
    t.className = `toast ${kind}`;
    t.setAttribute("role", kind === "error" ? "alert" : "status");
    t.textContent = msg;
    document.body.append(t);
    setTimeout(() => t.remove(), kind === "error" ? 6000 : 3500);
  }

  function hostLabel(url) {
    try {
      const u = new URL(url);
      return (u.host + u.pathname).replace(/\/$/, "") || url;
    } catch {
      return url || "";
    }
  }

  function plural(n, word) {
    return `${n} ${word}${n === 1 ? "" : "s"}`;
  }

  function fmtDate(val) {
    if (!val) return "—";
    const d = new Date(val);
    return Number.isNaN(d.getTime()) ? String(val) : dateFmt.format(d);
  }

  function formatCountry(t) {
    const cc = t.placeOfPerformance?.countryCode || t.portalCountry || "";
    return COUNTRY_LABEL[cc] || cc || "EU";
  }

  // --- Portal Cards + Live Summary -----------------------------------------------------------
  let activeStateFilter = "";

  function applyStateFilter() {
    document.querySelectorAll("#state-filters .chip-btn").forEach((btn) => {
      btn.classList.toggle("active", (btn.dataset.stateFilter || "") === activeStateFilter);
    });
    document.querySelectorAll("#grid .card").forEach((card) => {
      const match = !activeStateFilter || card.dataset.state === activeStateFilter;
      card.hidden = !match;
    });
  }

  async function refreshCard(id) {
    const r = await fetch(`/ui/portals/${encodeURIComponent(id)}/card`);
    const old = byId(`card-${id}`);
    if (r.status === 404) {
      old?.remove();
      scheduleSummary();
      scheduleTenders();
      return;
    }
    const tpl = document.createElement("template");
    tpl.innerHTML = (await r.text()).trim();
    const card = tpl.content.firstElementChild;
    if (old) old.replaceWith(card);
    else {
      byId("empty")?.remove();
      byId("grid")?.prepend(card);
    }
    applyStateFilter();
    card.classList.add("flash");
    setTimeout(() => card.classList.remove("flash"), 500);
    recount();
  }

  function scheduleCard(id) {
    clearTimeout(pendingCards.get(id));
    pendingCards.set(id, setTimeout(() => refreshCard(id), 250));
  }

  function renderActivityChart(activity) {
    if (!activity) return;
    const totalEl = byId("activity-total");
    const chartEl = byId("activity-chart");
    if (totalEl) totalEl.textContent = activity.total ?? 0;
    if (!chartEl || !Array.isArray(activity.days)) return;
    const maxCount = Math.max(1, ...activity.days.map((d) => d.count || 0));
    const cols = activity.days.map((d) => {
      const col = document.createElement("div");
      if (!d.count) col.className = "zero";
      col.title = `${d.weekday} (${d.date}): ${d.count} updates`;
      const bar = document.createElement("i");
      const pct = d.count > 0 ? Math.max(8, Math.round((d.count * 100) / maxCount)) : 8;
      bar.style.setProperty("--size", `${pct}%`);
      const lbl = document.createElement("span");
      lbl.textContent = d.label;
      col.append(bar, lbl);
      return col;
    });
    chartEl.setAttribute("aria-label", activity.days.map((d) => `${d.weekday} ${d.count}`).join(", ") + " updates");
    chartEl.replaceChildren(...cols);
  }

  let summaryTimer = null;
  async function refreshSummary() {
    try {
      const s = await api("GET", "/ui/summary");
      if (!s) return;
      for (const k of ["serving", "healing", "integrating", "failed"]) {
        const el = byId(`n-${k}`);
        if (el) el.textContent = s[k];
      }
      if (byId("n-tenders")) byId("n-tenders").textContent = s.tenders;
      if (byId("n-avg-usd")) byId("n-avg-usd").textContent = `$${Number(s.avg_usd || 0).toFixed(3)}`;
      if (byId("n-projected")) byId("n-projected").textContent = `$${Math.round(s.projected || 0)}`;
      if (s.activity) renderActivityChart(s.activity);
    } catch {}
  }

  function scheduleSummary() {
    clearTimeout(summaryTimer);
    summaryTimer = setTimeout(refreshSummary, 250);
  }

  function recount() {
    for (const s of ["serving", "healing", "integrating", "failed"]) {
      const el = byId(`n-${s}`);
      if (el) el.textContent = document.querySelectorAll(`.card.state-${s}`).length;
    }
    scheduleSummary();
  }

  // --- SSE Events & Live Stream --------------------------------------------------------------
  function describe(ev) {
    const d = ev.data || {};
    switch (ev.kind) {
      case "state": return `→ ${d.to}${d.reason ? " · " + d.reason : ""}`;
      case "log": return d.msg;
      case "judgment": return `Jev: ${(d.question || "").slice(0, 60)} → ${d.answer}`;
      case "recipe": return d.reason === "repaired" ? "Recipe patched" : "Recipe written";
      case "verify": return `Verified sample: ${d.ok ? "ok" : "rejected"}`;
      case "compare": return d.ok ? `Repair #${d.n} ready: Before vs after available` : `Repair #${d.n} failed`;
      default: return null;
    }
  }

  function feed(ev) {
    const text = describe(ev);
    const list = byId("feed");
    if (!text || !list) return;
    const li = document.createElement("li");
    const host = document.querySelector(`#card-${ev.portal_id} .host`)?.textContent || ev.portal_id || "system";
    const b = document.createElement("b");
    b.textContent = host;
    const span = document.createElement("span");
    span.textContent = text;
    li.append(b, span);
    list.prepend(li);
    while (list.children.length > 14) list.lastChild.remove();
  }

  /* Demo stage: the whole story (portals, what happened, what it was worth) on one screen, from live events. */
  const Stage = (() => {
    let on = false, t0 = 0, clock = null, mode = "replay", base = {};
    let portals = new Map(), spent = {}, durations = [], healStart = {};
    const n = { noticed: 0, repairs: 0, human: 0, kept: 0 };
    const LABEL = { serving: "delivering", healing: "repairing…", failed: "needs a person", integrating: "learning…" };
    const host = (u) => { try { const x = new URL(u); return x.pathname.includes("/lab/") ? x.pathname.split("/lab/")[1].replace(/\/$/, "") : x.host; } catch { return u; } };
    const fmtT = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
    function bump(id, text) {
      const el = byId(id); if (!el || el.textContent === String(text)) return;
      el.textContent = text; const box = el.parentElement; box.classList.remove("bump"); void box.offsetWidth; box.classList.add("bump");
    }
    function values() {
      let tenders = 0; for (const p of portals.values()) tenders += p.tenderCount || 0;
      bump("sv-tenders", tenders); bump("sv-kept", n.kept); bump("sv-noticed", n.noticed); bump("sv-repairs", n.repairs);
      bump("sv-human", n.human);
      bump("sv-ttr", durations.length ? `${Math.round(durations.reduce((a, b) => a + b, 0) / durations.length)} s` : "–");
      const usd = Object.values(spent).reduce((a, b) => a + b, 0);
      bump("sv-usd", "$" + usd.toFixed(usd < 1 ? 3 : 2)); bump("sv-hours", `${n.repairs * 2} h`);
    }
    function tiles() {
      const box = byId("stage-portals"); if (!box) return; box.textContent = "";
      for (const [id, p] of portals) {
        const b = document.createElement("button"); b.type = "button"; b.className = `sp ${p.state}`; b.title = p.url;
        b.addEventListener("click", () => window.open(`/portals/${encodeURIComponent(id)}/theater`, "_blank"));
        const name = document.createElement("b"); name.textContent = host(p.url);
        const st = document.createElement("span"); st.className = "sp-state"; st.textContent = LABEL[p.state] || p.state;
        const note = document.createElement("span"); note.className = "sp-note";
        note.textContent = p.state === "serving" ? `${p.tenderCount || 0} tenders` : (p.reason || "");
        b.append(name, st, note); box.append(b);
      }
    }
    function say(step, text) {
      const ol = byId("stage-story"); if (!ol) return;
      const li = document.createElement("li"); li.className = step || "";
      const t = document.createElement("time"); t.textContent = fmtT(Date.now() - t0);
      li.append(t, document.createTextNode(text)); ol.prepend(li);
    }
    async function load(filterLab) {
      const all = (await api("GET", "/portals")) || [];
      portals = new Map(all.filter((p) => !filterLab || p.url.includes("/lab/")).map((p) => [p.id, p]));
      n.human = [...portals.values()].filter((p) => p.state === "failed").length;
      await Promise.all([...portals.keys()].filter((id) => !(id in base)).map(async (id) => {
        try { base[id] = (await api("GET", `/portals/${encodeURIComponent(id)}/cost`))?.total_usd || 0; } catch { base[id] = 0; }
      }));
      tiles(); values();
    }
    async function start(m) {
      const box = byId("stage"); if (!box) return;
      mode = m; base = mode === "live" ? {} : Object.fromEntries([...portals.keys()].map((id) => [id, 0]));
      on = true; t0 = Date.now(); spent = {}; durations = []; healStart = {}; Object.assign(n, { noticed: 0, repairs: 0, human: 0, kept: 0 });
      byId("stage-mode").textContent = mode === "live" ? "LIVE · REAL MODEL CALLS" : "REPLAY OF THE REAL FLOW · NO MODEL CALLS";
      byId("sv-usd-label").textContent = mode === "live" ? "Model cost, live (measured)" : "Model cost (replayed, typical)";
      byId("stage-title").textContent = mode === "live" ? "Breaking portals live" : "One night at the watchtower";
      byId("stage-story").textContent = ""; box.hidden = false; window.scrollTo({ top: 0, behavior: "smooth" });
      clearInterval(clock); clock = setInterval(() => { byId("stage-clock").textContent = fmtT(Date.now() - t0); }, 500);
      await load(true);
    }
    function stop() { on = false; clearInterval(clock); const box = byId("stage"); if (box) box.hidden = true; }
    async function onEvent(ev) {
      if (!on) return;
      const d = ev.data || {};
      if (ev.kind === "story") { say(d.step, d.text); return; }
      if (ev.kind === "cost" && ev.portal_id && portals.has(ev.portal_id)) {
        spent[ev.portal_id] = Math.max(0, (d.total_usd || 0) - (base[ev.portal_id] || 0)); values(); return;
      }
      if (ev.kind !== "state" || !ev.portal_id) return;
      let p = portals.get(ev.portal_id);
      if (!p) { try { p = await api("GET", `/portals/${encodeURIComponent(ev.portal_id)}`); } catch { return; } if (!p.url.includes("/lab/")) return; }
      const name = host(p.url);
      if (d.from === "serving" && d.to === "healing") {
        n.noticed += 1; n.kept += p.tenderCount || 0; healStart[ev.portal_id] = Date.now();
        if (mode === "live") say("notice", `Pulse noticed ${name} without a refresh: ${d.reason || "it changed"} Its ${p.tenderCount || 0} tenders stay served.`);
      }
      if (d.from === "healing" && d.to === "serving" && healStart[ev.portal_id]) {
        const secs = (Date.now() - healStart[ev.portal_id]) / 1000;
        n.repairs += 1; durations.push(secs); delete healStart[ev.portal_id];
        if (mode === "live") say("repaired", `${name} repaired itself, ${Math.round(secs)} s after the break was noticed. Open its Theater for before vs after.`);
      }
      if (d.to === "failed" && d.from !== "failed") {
        n.human += 1;
        if (mode === "live") say("honest", `${name} needs a person: ${d.reason || "no reason given"}`);
      }
      try { p = await api("GET", `/portals/${encodeURIComponent(ev.portal_id)}`); } catch {}
      portals.set(ev.portal_id, { ...p, state: d.to, reason: d.reason || p.reason });
      tiles(); values();
    }
    byId("stage-close")?.addEventListener("click", stop);
    return { start, stop, onEvent, reload: async () => { if (mode === "replay") base = {}; await load(true); if (mode === "replay") for (const id of portals.keys()) base[id] = 0; } };
  })();

  function connect(onEvent) {
    const es = new EventSource("/events");
    es.onmessage = (m) => onEvent(JSON.parse(m.data));
    for (const k of ["state", "run", "log", "judgment", "recipe", "verify", "page", "explore", "cost", "llm", "compare", "story"]) {
      es.addEventListener(k, (m) => onEvent(JSON.parse(m.data)));
    }
    return es;
  }

  // --- Portal-Scoped Tenders Modal (#tenders-dialog) -----------------------------------------
  let modalTenders = [];
  function renderModalTenders() {
    const box = byId("td-list");
    const q = (byId("td-search")?.value || "").trim().toLowerCase();
    if (!box) return;
    const rows = modalTenders.filter((t) =>
      `${t.title || ""} ${t.buyerName || ""} ${t.referenceNumber || ""} ${(t.cpvCodes || []).join(" ")}`.toLowerCase().includes(q)
    );
    box.textContent = "";
    if (!rows.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "No tenders match.";
      box.append(p);
      return;
    }
    for (const t of rows.slice(0, 80)) {
      const card = document.createElement("div");
      card.className = "td-row";
      const top = document.createElement("div");
      top.className = "td-top";
      const st = document.createElement("span");
      st.className = `pill-sm ${t.status || "open"}`;
      st.textContent = t.status || "open";
      const link = document.createElement("a");
      link.href = t.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = t.title || t.id;
      top.append(st, link);
      const meta = document.createElement("div");
      meta.className = "td-meta";
      const dl = t.submissionDeadline ? fmtDate(t.submissionDeadline) : "—";
      meta.textContent = `${t.buyerName || "—"} · Deadline: ${dl}${t.cpvCodes?.length ? " · CPV " + t.cpvCodes.join(", ") : ""}`;
      card.append(top, meta);
      if (t.documents?.length) {
        const docs = document.createElement("div");
        docs.className = "td-docs";
        for (const d of t.documents) {
          const a = document.createElement("a");
          a.className = "doc-chip";
          a.href = d.path;
          a.target = "_blank";
          a.rel = "noopener";
          a.textContent = `📄 ${d.fileName} (${Math.max(1, Math.round((d.sizeBytes || 0) / 1024))} KB)`;
          docs.append(a);
        }
        card.append(docs);
      }
      box.append(card);
    }
  }

  async function openTenders(pid, host) {
    const dlg = byId("tenders-dialog");
    if (!dlg) return;
    byId("td-title").textContent = host ? `Tenders · ${host}` : "Tenders";
    byId("td-sub").textContent = "Loading…";
    byId("td-list").textContent = "";
    if (byId("td-search")) byId("td-search").value = "";
    dlg.showModal();
    try {
      modalTenders = (await api("GET", `/portals/${encodeURIComponent(pid)}/tenders`)) || [];
      const docTotal = modalTenders.reduce((a, t) => a + (t.documents?.length || 0), 0);
      byId("td-sub").textContent = `${modalTenders.length} tenders · ${docTotal} verified documents`;
      renderModalTenders();
    } catch (err) {
      byId("td-sub").textContent = err.message;
    }
  }

  function wireSharedModals() {
    byId("td-close")?.addEventListener("click", () => byId("tenders-dialog")?.close());
    byId("td-search")?.addEventListener("input", renderModalTenders);
    byId("tenders-dialog")?.addEventListener("click", (e) => {
      if (e.target.id === "tenders-dialog") e.target.close();
    });
    document.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-tenders]");
      if (btn) openTenders(btn.dataset.tenders, btn.dataset.host);
    });
    byId("close-detail")?.addEventListener("click", () => byId("detail")?.close());
    byId("detail")?.addEventListener("click", (e) => {
      if (e.target.id === "detail") e.target.close();
    });
    syncSavedBadge();
  }
  wireSharedModals();

  // --- Unified Briefly SPA Views + Live Tender Search/Saved/Repair ---------------------------
  let liveTenders = [];
  let totalTenders = 0;
  let currentView;
  let activeTender = null;
  let tendersTimer = null;

  function setOptRow(key, value) {
    const els = document.querySelectorAll(`[data-opt="${key}"]`);
    const show = Boolean(value);
    els.forEach((el) => { el.hidden = !show; });
    if (show && els[1]) els[1].textContent = value;
  }

  function updateSaveButton(button, tender, iconOnly = false) {
    const isSaved = saved.has(tender._key);
    button.setAttribute("aria-pressed", String(isSaved));
    if (iconOnly) {
      button.setAttribute("aria-label", `${isSaved ? "Unsave" : "Save"} ${tender.title || tender.id}`);
      button.firstElementChild.textContent = isSaved ? "★" : "☆";
    } else {
      button.textContent = isSaved ? "Saved tender ★" : "Save tender";
    }
  }

  function toggleSaved(tender) {
    if (!tender) return;
    if (saved.has(tender._key)) saved.delete(tender._key);
    else saved.add(tender._key);
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify([...saved])); } catch {}
    syncSavedBadge();
    const detail = byId("detail");
    const detailSave = byId("detail-save");
    if (detail?.open && detailSave) updateSaveButton(detailSave, tender);
    const list = byId("tender-list");
    const row = [...(list?.children || [])].find((item) => item.dataset.key === tender._key);
    if (row) updateSaveButton(row.querySelector(".save"), tender, true);
    if (currentView === "saved" && !saved.has(tender._key) && !detail?.open) {
      renderTenders();
    }
  }

  function openTenderDetail(tender) {
    const detail = byId("detail");
    if (!detail) return;
    activeTender = tender;
    byId("detail-title").textContent = tender.title || tender.id;
    const statusTag = byId("detail-status");
    const status = tender.status || "open";
    statusTag.textContent = status;
    statusTag.dataset.status = status;
    byId("detail-description").textContent = tender.description || `Published on ${tender.portalHost}. Open the source page or documents below for full specifications.`;
    byId("detail-buyer").textContent = tender.buyerName || tender.portalHost || "—";
    byId("detail-portal").textContent = tender.portalHost || "—";
    byId("detail-country").textContent = formatCountry(tender);
    byId("detail-published").textContent = fmtDate(tender.publicationDate);
    byId("detail-deadline").textContent = fmtDate(tender.submissionDeadline);
    setOptRow("ref", tender.referenceNumber || "");
    setOptRow("proc", tender.procedureType || "");
    setOptRow("nature", tender.contractNature || "");
    setOptRow("cpv", tender.cpvCodes?.length ? tender.cpvCodes.join(", ") : "");

    const docsEl = byId("detail-docs");
    const links = [];
    for (const d of tender.documents || []) {
      const a = document.createElement("a");
      a.className = "detail-doc-link";
      a.href = d.path;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = `📄 ${d.fileName} (${Math.max(1, Math.round((d.sizeBytes || 0) / 1024))} KB)`;
      links.push(a);
    }
    if (tender.url) {
      const src = document.createElement("a");
      src.className = "detail-doc-link";
      src.href = tender.url;
      src.target = "_blank";
      src.rel = "noopener";
      src.textContent = "Source page ↗";
      links.push(src);
    }
    docsEl.replaceChildren(...links);
    docsEl.hidden = links.length === 0;
    updateSaveButton(byId("detail-save"), tender);
    detail.showModal();
  }

  function rebuildTenderFilters() {
    const countrySel = byId("country");
    const portalSel = byId("portal-filter");
    if (countrySel) {
      const prev = countrySel.value;
      countrySel.replaceChildren(new Option("All countries", ""));
      for (const name of [...new Set(liveTenders.map(formatCountry))].sort()) {
        countrySel.add(new Option(name, name));
      }
      if ([...countrySel.options].some((o) => o.value === prev)) countrySel.value = prev;
    }
    if (portalSel) {
      const prev = portalSel.value;
      portalSel.replaceChildren(new Option("All portals", ""));
      const seenPortals = new Map();
      for (const t of liveTenders) {
        if (!seenPortals.has(t.portalId)) seenPortals.set(t.portalId, t.portalHost || t.portalId);
      }
      for (const [pid, label] of [...seenPortals.entries()].sort((a, b) => a[1].localeCompare(b[1]))) {
        portalSel.add(new Option(label, pid));
      }
      if ([...portalSel.options].some((o) => o.value === prev)) portalSel.value = prev;
    }
  }

  function renderTenders() {
    const list = byId("tender-list");
    const tpl = byId("tender-template");
    if (!list || !tpl) return;
    const query = (byId("search")?.value || "").trim().toLocaleLowerCase();
    const wantPortal = byId("portal-filter")?.value || "";
    const wantCountry = byId("country")?.value || "";
    const wantStatus = byId("status-filter")?.value || "";

    const visible = liveTenders.filter((t) => {
      if (currentView === "saved" && !saved.has(t._key)) return false;
      if (wantPortal && t.portalId !== wantPortal) return false;
      if (wantCountry && formatCountry(t) !== wantCountry) return false;
      if (wantStatus && (t.status || "open") !== wantStatus) return false;
      if (!query) return true;
      const hay = `${t.title || ""} ${t.buyerName || ""} ${t.portalHost || ""} ${t.referenceNumber || ""} ${formatCountry(t)} ${(t.cpvCodes || []).join(" ")}`.toLocaleLowerCase();
      return hay.includes(query);
    });

    const limit = currentView === "overview" ? 8 : 150;
    const shown = visible.slice(0, limit);
    const rows = shown.map((t) => {
      const row = tpl.content.firstElementChild.cloneNode(true);
      row.dataset.key = t._key;
      const titleBtn = row.querySelector(".open-tender");
      titleBtn.textContent = t.title || t.id;
      titleBtn.addEventListener("click", () => openTenderDetail(t));
      const docCount = (t.documents || []).length;
      row.querySelector(".buyer").textContent = `${t.buyerName || t.portalHost || "—"}${docCount ? ` · ${plural(docCount, "file")}` : ""}`;
      row.querySelector(".portal-pill").textContent = t.portalHost || "";
      row.querySelector(".country").textContent = formatCountry(t);
      row.querySelector(".deadline").textContent = t.submissionDeadline
        ? `Due ${fmtDate(t.submissionDeadline)}`
        : (t.publicationDate ? `Pub ${fmtDate(t.publicationDate)}` : "—");
      const tag = row.querySelector(".tag");
      const status = t.status || "open";
      tag.dataset.status = status;
      tag.textContent = status;
      const saveBtn = row.querySelector(".save");
      updateSaveButton(saveBtn, t, true);
      saveBtn.addEventListener("click", () => toggleSaved(t));
      return row;
    });

    if (!rows.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      if (currentView === "saved" && !saved.size) {
        empty.textContent = "No saved tenders yet. Star an opportunity in the tender list to keep it here.";
      } else if (!liveTenders.length) {
        empty.textContent = "No tenders delivered yet. Add a portal or wait for attached portals to finish delivering.";
      } else {
        empty.textContent = "No tenders match these filters.";
      }
      rows.push(empty);
    }

    list.replaceChildren(...rows);
    const resEl = byId("results");
    if (resEl) {
      resEl.textContent = visible.length > shown.length
        ? `Showing ${shown.length} of ${plural(visible.length, "matching tender")}.`
        : `${plural(visible.length, "tender")}${totalTenders > liveTenders.length ? ` (of ${totalTenders} delivered)` : ""}.`;
    }
    const moreWrap = byId("tender-more");
    if (moreWrap) {
      moreWrap.hidden = !(currentView === "overview" && visible.length > limit);
      const moreLink = byId("tender-more-link");
      if (moreLink) moreLink.textContent = `View all ${visible.length} tenders →`;
    }
  }

  async function loadTenders() {
    try {
      const data = await api("GET", "/ui/tenders");
      if (!data) return;
      totalTenders = data.total ?? (data.tenders || []).length;
      liveTenders = (data.tenders || []).map((t) => ({ ...t, _key: `${t.portalId}:${t.id}` }));
      rebuildTenderFilters();
      renderTenders();
    } catch {}
  }

  function scheduleTenders() {
    clearTimeout(tendersTimer);
    tendersTimer = setTimeout(loadTenders, 400);
  }

  // --- Before vs After Repair View (#repair/{pid}/{n}) ---------------------------------------
  function setRepairLayout() {
    const changed = currentView !== undefined && currentView !== "repair";
    currentView = "repair";
    byId("overview").hidden = true;
    byId("portals-section").hidden = true;
    byId("tender-section").hidden = true;
    byId("repair-view").hidden = false;
    if (byId("traces")) byId("traces").hidden = true;
    if (byId("live-feed-panel")) byId("live-feed-panel").hidden = true;
    byId("location").textContent = "Before vs after";
    document.querySelectorAll("nav a[data-nav]").forEach((link) => {
      if (link.dataset.nav === "portals") link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    if (changed) {
      window.scrollTo(0, 0);
      byId("main").focus({ preventScroll: true });
    }
  }

  function readsSummary(side) {
    if (!side) return "reads the page";
    if (side.source === "json_api") return "reads a JSON feed (not drawn on the page)";
    return `reads ${plural(side.items, "tender row")}`;
  }

  async function showRepairView(rest) {
    setRepairLayout();
    const [pidRaw, nRaw] = rest.split("/");
    const pid = decodeURIComponent(pidRaw || "");
    const theaterLink = byId("repair-theater-link");
    if (theaterLink) theaterLink.href = `/portals/${encodeURIComponent(pid)}/theater`;
    let portal, repairs;
    try {
      [portal, repairs] = await Promise.all([
        api("GET", `/portals/${encodeURIComponent(pid)}`),
        api("GET", `/portals/${encodeURIComponent(pid)}/repairs`),
      ]);
    } catch {
      byId("repair-portal").textContent = "";
      byId("repair-summary").textContent = "Portal not found or could not load repair history.";
      return;
    }
    byId("repair-portal").textContent = hostLabel(portal.url);
    if (!repairs?.length) {
      byId("repair-summary").textContent = "This portal has not needed a repair yet.";
      byId("frame-before").removeAttribute("src");
      byId("frame-after").removeAttribute("src");
      byId("repair-diff").replaceChildren();
      byId("repair-pick").closest(".filters").hidden = true;
      return;
    }
    const pick = byId("repair-pick");
    pick.replaceChildren(...repairs.map((r) => new Option(`Repair #${r.n} · ${whenFmt.format(new Date(r.at))} · ${r.ok ? "healed" : "failed"}`, r.n)));
    const r = repairs.find((x) => x.n === Number(nRaw)) || repairs[0];
    pick.value = String(r.n);
    pick.closest(".filters").hidden = repairs.length < 2;
    pick.onchange = () => { location.hash = `repair/${encodeURIComponent(pid)}/${pick.value}`; };

    const when = whenFmt.format(new Date(r.at));
    byId("repair-summary").textContent = r.ok
      ? `On ${when}, this portal changed how its page is built. Watchtower noticed, kept serving the last correct tenders, and repaired itself in ${plural(r.attempts, "attempt")}.`
      : `On ${when}, this portal changed and Watchtower could not repair it automatically. A person should take a look.`;
    byId("cap-before").textContent = `· old recipe ${readsSummary(r.before)} · ${plural(r.before.changed, "part")} changed or gone`;
    byId("cap-after").textContent = r.ok
      ? `· new recipe ${readsSummary(r.after)} · ${plural(r.after.changed, "part")} new or changed`
      : "· no working recipe yet";
    const base = `/portals/${encodeURIComponent(pid)}/repairs/${r.n}`;
    byId("frame-before").src = `${base}/before.html`;
    byId("frame-after").src = `${base}/after.html`;
    const items = (r.diff || []).slice(0, 12).map((d) => {
      const li = document.createElement("li");
      li.textContent = d;
      return li;
    });
    if (!r.ok && r.reason) {
      const li = document.createElement("li");
      li.textContent = r.reason;
      items.push(li);
    }
    byId("repair-diff").replaceChildren(...items);
  }

  const VIEWS = { overview: "Overview", portals: "Portals", tenders: "Tenders", saved: "Saved" };

  function renderView() {
    if (!byId("overview")) return;
    const requested = location.hash.slice(1);
    if (requested === "main" && currentView) return;
    if (requested.startsWith("repair/")) {
      showRepairView(requested.slice("repair/".length));
      return;
    }
    byId("repair-view").hidden = true;
    const view = Object.hasOwn(VIEWS, requested) ? requested : "overview";
    const changed = currentView !== undefined && currentView !== view;
    currentView = view;

    byId("location").textContent = VIEWS[view];
    byId("overview").hidden = view !== "overview";
    byId("portals-section").hidden = !(view === "overview" || view === "portals");
    byId("portals-heading").hidden = view !== "overview";
    byId("portals-page-heading").hidden = view !== "portals";

    byId("tender-section").hidden = !(view === "overview" || view === "tenders" || view === "saved");
    byId("tender-heading").hidden = view !== "overview";
    const pageHeading = byId("tender-page-heading");
    pageHeading.hidden = view === "overview";
    pageHeading.textContent = view === "saved" ? "Saved tenders" : "Tenders";
    byId("tender-section").setAttribute("aria-labelledby", view === "overview" ? "tender-heading" : "tender-page-heading");

    if (byId("traces")) byId("traces").hidden = !(view === "overview" || view === "portals");
    if (byId("live-feed-panel")) byId("live-feed-panel").hidden = !(view === "overview" || view === "portals");

    document.querySelectorAll("nav a[data-nav]").forEach((link) => {
      if (link.dataset.nav === view) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });

    applyStateFilter();
    renderTenders();
    if (changed) {
      window.scrollTo(0, 0);
      byId("main").focus({ preventScroll: true });
    }
  }

  function findTender() {
    if (!byId("search")) return;
    if (currentView === "tenders") {
      byId("search").focus();
      return;
    }
    window.addEventListener("hashchange", () => byId("search")?.focus(), { once: true });
    location.hash = "tenders";
  }

  // --- Morning Dashboard Entry Point ---------------------------------------------------------
  function morning() {
    byId("add-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = e.target.url;
      const btn = e.target.querySelector("button");
      btn.disabled = true;
      try {
        const { id } = await api("POST", "/portals", { url: input.value.trim() });
        input.value = "";
        await refreshCard(id);
        toast("Portal added — attaching in the background.");
      } catch (err) {
        toast(err.message, "error");
      } finally {
        btn.disabled = false;
      }
    });

    document.addEventListener("click", async (e) => {
      const chip = e.target.closest("[data-state-filter]");
      if (chip) {
        activeStateFilter = chip.dataset.stateFilter || "";
        applyStateFilter();
      }
      const tile = e.target.closest(".metric-tile[data-filter-state]");
      if (tile) {
        activeStateFilter = tile.dataset.filterState || "";
        applyStateFilter();
      }
      const t = e.target.closest("button");
      if (!t) return;
      if (t.dataset.refresh) {
        t.disabled = true;
        try {
          await api("POST", `/portals/${encodeURIComponent(t.dataset.refresh)}/refresh`);
          toast("Reading the portal now");
        } catch (err) {
          toast(err.message, "error");
        } finally {
          t.disabled = false;
        }
        scheduleCard(t.dataset.refresh);
      }
      if (t.dataset.break) {
        t.disabled = true;
        const r = await api("POST", `/portals/${encodeURIComponent(t.dataset.break)}/break`).catch((err) => toast(err.message, "error"));
        if (r) {
          toast(`Chaos Lab broke the portal: ${r.mutation}. Watch it heal.`);
          t.textContent = `Broke: ${r.mutation}`;
        }
        setTimeout(() => { t.disabled = false; t.textContent = "Break it"; }, 4000);
      }
      if (t.dataset.delete && confirm("Remove this portal and its tenders?")) {
        await api("DELETE", `/portals/${encodeURIComponent(t.dataset.delete)}`)
          .then(() => {
            byId(`card-${t.dataset.delete}`)?.remove();
            recount();
            scheduleTenders();
            toast("Portal removed");
          })
          .catch((err) => toast(err.message, "error"));
      }
    });

    byId("brief-btn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const box = byId("brief-box");
      const out = byId("brief-text");
      const audio = byId("brief-audio");
      btn.disabled = true;
      btn.textContent = "Preparing…";
      try {
        out.textContent = await (await fetch("/briefing.txt")).text();
        box.hidden = false;
        const r = await fetch("/briefing.mp3");
        if (r.ok) {
          audio.src = URL.createObjectURL(await r.blob());
          audio.hidden = false;
          audio.controls = true;
          await audio.play();
        }
      } catch (err) {
        toast(err.message, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "▶ Morning briefing";
      }
    });

    byId("timelapse-btn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        await Stage.start("replay");
        const res = await api("POST", "/demo/timelapse");
        const liveIds = new Set((res?.portals || []).map((p) => p.id));
        document.querySelectorAll(".card").forEach((c) => {
          if (!liveIds.has(c.dataset.id) && c.textContent.includes("/lab/")) c.remove();
        });
        await Stage.reload();
        if (res?.portals) await Promise.all(res.portals.map((p) => refreshCard(p.id)));
        await refreshSummary();
        await loadTenders();
      } catch (err) {
        toast(err.message, "error");
      } finally {
        setTimeout(() => { btn.disabled = false; }, 8000);
      }
    });

    byId("chaos-btn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        await Stage.start("live");
        const res = await api("POST", "/demo/chaos");
        const b = res?.broken?.[0];
        if (!b) toast("No serving lab portal to break: run the replay first, or attach /lab/ssr/.", "error");
        else {
          toast(`Broke ${b.slug} live (${b.mutation}). Watch the card: amber, then green.`);
          const card = byId(`card-${b.id}`);
          card?.scrollIntoView({ behavior: "smooth", block: "center" });
          card?.classList.add("card-spotlight");
          setTimeout(() => card?.classList.remove("card-spotlight"), 6000);
        }
      } catch (err) {
        toast(err.message, "error");
      } finally {
        setTimeout(() => { btn.disabled = false; }, 5000);
      }
    });

    byId("search")?.addEventListener("input", renderTenders);
    byId("portal-filter")?.addEventListener("change", renderTenders);
    byId("country")?.addEventListener("change", renderTenders);
    byId("status-filter")?.addEventListener("change", renderTenders);
    byId("search-shortcut")?.addEventListener("click", findTender);
    byId("detail-save")?.addEventListener("click", () => toggleSaved(activeTender));
    byId("detail")?.addEventListener("close", () => {
      if (currentView === "saved" && activeTender && !saved.has(activeTender._key)) {
        renderTenders();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.altKey && !event.ctrlKey && !event.metaKey && event.code === "Slash" && !byId("detail")?.open && !byId("tenders-dialog")?.open) {
        event.preventDefault();
        findTender();
      }
    });

    window.addEventListener("hashchange", renderView);
    renderView();
    loadTenders();

    connect((ev) => {
      if (ev.portal_id && ["state", "run", "compare"].includes(ev.kind)) {
        scheduleCard(ev.portal_id);
        scheduleTenders();
      }
      if (ev.kind === "compare" && currentView === "repair") {
        showRepairView(location.hash.slice("#repair/".length));
      }
      if (ev.kind === "cost") scheduleSummary();
      Stage.onEvent(ev);
      if (ev.kind !== "story") feed(ev);
    });
    setInterval(() => {
      document.querySelectorAll(".card").forEach((c) => scheduleCard(c.dataset.id));
    }, 60000);
  }

  /* Model calls panel: recent LLM and Jev calls as MLflow recorded them. */
  function traces() {
    const box = byId("traces");
    if (!box) return;
    const seen = new Set();
    let timer = null;
    const money = (v) => (v == null ? "–" : v >= 0.01 ? "$" + v.toFixed(3) : "$" + v.toFixed(6));
    const ago = (ms) => {
      const s = Math.max(0, (Date.now() - ms) / 1000);
      return s < 60 ? Math.round(s) + " s ago" : s < 3600 ? Math.round(s / 60) + " min ago" : Math.round(s / 3600) + " h ago";
    };
    function message(body, text) {
      body.textContent = "";
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 7;
      td.className = "muted";
      td.textContent = text;
      tr.append(td);
      body.append(tr);
    }
    async function load() {
      const body = byId("t-rows");
      let data;
      try { data = await (await fetch("/traces")).json(); } catch { message(body, "Could not reach the trace store."); return; }
      const rows = data.traces || [];
      for (const k of ["llm", "jev"]) {
        const rs = rows.filter((r) => r.kind === k);
        byId(`t-${k}-n`).textContent = rs.length;
        byId(`t-${k}-usd`).textContent = money(rs.reduce((a, r) => a + (r.usd || 0), 0));
      }
      if (!rows.length) {
        message(body, "No model calls yet. Add a portal or press Break it.");
        return;
      }
      body.textContent = "";
      for (const r of rows) {
        const tr = document.createElement("tr");
        if (seen.size && !seen.has(r.id)) tr.className = "new";
        const host = document.querySelector(`#card-${r.portal_id} .host`)?.textContent || r.portal_id || "–";
        const what = r.kind === "llm" ? `${r.purpose || ""} · ${r.model || ""}` : (r.purpose || "");
        const cells = [
          ago(r.at), "", host, what,
          `${r.tokens_in ?? "–"} / ${r.tokens_out ?? "–"}`,
          r.ms != null ? `${(r.ms / 1000).toFixed(1)} s` : "–", money(r.usd),
        ];
        cells.forEach((c, i) => {
          const td = document.createElement("td");
          if (i === 1) {
            const k = document.createElement("span");
            k.className = `k k-${r.kind}`;
            k.textContent = r.kind === "llm" ? "LLM" : "Jev";
            td.append(k);
          } else if (i === 3) {
            const w = document.createElement("span");
            w.textContent = c;
            td.title = c;
            td.append(w);
          } else td.textContent = c;
          if (i === 3) td.className = "what";
          if (i >= 4) td.className = "r";
          tr.append(td);
        });
        body.append(tr);
      }
      rows.forEach((r) => seen.add(r.id));
    }
    const soon = () => { clearTimeout(timer); timer = setTimeout(load, 2500); };
    load();
    setInterval(load, 30000);
    connect((ev) => { if (["llm", "judgment"].includes(ev.kind)) soon(); });
  }

  return { api, connect, morning, refreshCard, toast, traces, $ };
})();
