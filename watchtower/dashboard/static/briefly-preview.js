const tenders = [
  { id: "library", title: "Public library refurbishment", buyer: "Nordstadt Procurement", country: "Germany", deadline: "2026-12-18", status: "New", description: "Refurbishment of a municipal library, including accessible reading rooms, lighting and public reception areas." },
  { id: "buses", title: "Electric buses for regional routes", buyer: "Marchés du Littoral", country: "France", deadline: "2027-01-12", status: "Updated", description: "Supply and maintenance of battery-electric buses, including charging infrastructure and operator training." },
  { id: "water", title: "Water network monitoring", buyer: "Comune di Vallechiara", country: "Italy", deadline: "2027-01-19", status: "New", description: "Deployment of pressure and flow sensors to monitor a municipal water distribution network." },
  { id: "solar", title: "Solar panels for public schools", buyer: "Nordstadt Procurement", country: "Germany", deadline: "2027-01-26", status: "Updated", description: "Design and installation of rooftop solar systems across four public schools." }
];

const portals = [
  { name: "Nordstadt Procurement", country: "Germany", status: "Monitoring" },
  { name: "Marchés du Littoral", country: "France", status: "Monitoring" },
  { name: "Comune di Vallechiara", country: "Italy", status: "Needs review", detail: "Sign-in required" }
];

const views = { overview: "Overview", tenders: "Tenders", saved: "Saved", portals: "Portals" };
const storageKey = "briefly-saved-tenders";
const byId = id => document.getElementById(id);
const dateFormat = new Intl.DateTimeFormat(document.documentElement.lang, {
  dateStyle: "medium",
  timeZone: "UTC"
});

let saved = new Set();
try {
  const stored = JSON.parse(localStorage.getItem(storageKey));
  if (Array.isArray(stored)) {
    saved = new Set(stored.filter(id => tenders.some(tender => tender.id === id)));
  }
} catch {
  // Saved tenders still work during this session if storage is unavailable.
}

const search = byId("search");
const country = byId("country");
const dialog = byId("detail");
const detailSave = byId("detail-save");
const tenderList = byId("tender-list");
let activeTender;
let currentView;

function formatDate(date) {
  return dateFormat.format(new Date(date));
}

function updateSaveButton(button, tender, iconOnly = false) {
  const isSaved = saved.has(tender.id);
  button.setAttribute("aria-pressed", String(isSaved));
  if (iconOnly) {
    button.setAttribute("aria-label", `Save ${tender.title}`);
    button.firstElementChild.textContent = isSaved ? "★" : "☆";
  } else {
    button.textContent = "Save tender";
  }
}

function toggleSaved(tender, button) {
  if (saved.has(tender.id)) saved.delete(tender.id);
  else saved.add(tender.id);

  try {
    localStorage.setItem(storageKey, JSON.stringify([...saved]));
  } catch {
    // Keep the current selection in memory.
  }
  byId("count-saved").textContent = saved.size;
  if (dialog.open) updateSaveButton(detailSave, tender);
  const row = [...tenderList.children].find(item => item.dataset.id === tender.id);
  if (row) updateSaveButton(row.querySelector(".save"), tender, true);

  if (currentView === "saved" && !saved.has(tender.id)) {
    if (!dialog.open) {
      renderTenders();
      (tenderList.querySelector(".save") ?? search).focus();
    } else if (button === detailSave) {
      byId("results").textContent = "Tender removed from saved.";
    }
  }
}

function openTender(tender) {
  activeTender = tender;
  byId("detail-title").textContent = tender.title;
  byId("detail-description").textContent = tender.description;
  byId("detail-buyer").textContent = tender.buyer;
  byId("detail-country").textContent = tender.country;
  byId("detail-deadline").textContent = formatDate(tender.deadline);
  updateSaveButton(detailSave, tender);
  dialog.showModal();
}

function renderTenders() {
  const query = search.value.trim().toLocaleLowerCase();
  const visible = tenders.filter(tender =>
    (currentView !== "saved" || saved.has(tender.id)) &&
    (!country.value || tender.country === country.value) &&
    `${tender.title} ${tender.buyer} ${tender.country}`.toLocaleLowerCase().includes(query)
  );

  const rows = visible.map(tender => {
    const row = byId("tender-template").content.firstElementChild.cloneNode(true);
    row.dataset.id = tender.id;
    const title = row.querySelector(".open-tender");
    title.textContent = tender.title;
    title.addEventListener("click", () => openTender(tender));
    row.querySelector(".buyer").textContent = tender.buyer;
    row.querySelector(".country").textContent = tender.country;
    row.querySelector(".deadline").textContent = `Due ${formatDate(tender.deadline)}`;
    const tag = row.querySelector(".tag");
    tag.dataset.status = tender.status;
    tag.textContent = tender.status;
    const save = row.querySelector(".save");
    updateSaveButton(save, tender, true);
    save.addEventListener("click", () => toggleSaved(tender, save));
    return row;
  });

  if (!rows.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = currentView === "saved" && !saved.size
      ? "No saved tenders yet. Save an opportunity from the tender list to see it here."
      : "No tenders match these filters.";
    rows.push(empty);
  }
  tenderList.replaceChildren(...rows);
  byId("results").textContent = `${visible.length} ${visible.length === 1 ? "tender" : "tenders"} shown.`;
}

function renderView() {
  const requested = location.hash.slice(1);
  if (requested === "main" && currentView) return;
  if (requested.startsWith("repair/")) {
    showRepairView(requested.slice("repair/".length));
    return;
  }
  byId("repair-view").hidden = true;
  const view = Object.hasOwn(views, requested) ? requested : "overview";
  const changed = currentView !== undefined && currentView !== view;
  currentView = view;
  byId("location").textContent = views[view];
  byId("overview").hidden = view !== "overview";
  byId("portals").hidden = view !== "portals";
  byId("tender-section").hidden = view === "portals";
  byId("tender-heading").hidden = view !== "overview";
  const pageHeading = byId("tender-page-heading");
  pageHeading.hidden = view === "overview";
  pageHeading.textContent = view === "saved" ? "Saved tenders" : "Tenders";
  byId("tender-section").setAttribute("aria-labelledby",
    view === "overview" ? "tender-heading" : "tender-page-heading"
  );
  document.querySelectorAll("nav a").forEach(link => {
    if (link.hash === `#${view}`) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  renderTenders();
  if (changed) {
    window.scrollTo(0, 0);
    byId("main").focus({ preventScroll: true });
  }
}

// --- Live portals from Watchtower (falls back to the sample portals when the API is not there) ----
const STATE_LABEL = { serving: "Monitoring", healing: "Healing", integrating: "Connecting", failed: "Needs review" };
const whenFormat = new Intl.DateTimeFormat(document.documentElement.lang, { dateStyle: "medium", timeStyle: "short" });
let livePortals = null;

function portalName(url) {
  try {
    const u = new URL(url);
    return (u.host + u.pathname).replace(/\/$/, "");
  } catch {
    return url;
  }
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

async function getJson(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

async function loadPortals() {
  try {
    const list = await getJson("/portals");
    livePortals = await Promise.all(list.map(async (p) => {
      let repairs = [];
      try { repairs = await getJson(`/portals/${encodeURIComponent(p.id)}/repairs`); } catch { /* older server: no repairs */ }
      return { ...p, repairs };
    }));
  } catch {
    livePortals = null;
  }
  renderPortals();
}

function statusTag(label) {
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.dataset.status = label;
  tag.textContent = label;
  return tag;
}

function portalRow(name, detail, label, repairHref) {
  const row = document.createElement("li");
  row.className = "portal-item";
  const text = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = name;
  const p = document.createElement("p");
  p.textContent = detail;
  text.append(strong, p);
  const side = document.createElement("div");
  side.className = "portal-actions";
  side.append(statusTag(label));
  if (repairHref) {
    const a = document.createElement("a");
    a.className = "compare-link";
    a.href = repairHref;
    a.textContent = "Before vs after";
    side.append(a);
  }
  row.append(text, side);
  return row;
}

function renderPortals() {
  const live = Array.isArray(livePortals);
  byId("source-label").textContent = live ? "Live · Watchtower" : "Demo workspace";
  byId("source-dot").classList.toggle("live", live);
  byId("portal-source").textContent = live ? "Live from Watchtower" : "Sample data · start Watchtower to see your portals";
  const rows = [];
  if (live) {
    for (const p of livePortals) {
      const last = p.repairs[0];
      const bits = [plural(p.tenderCount, "tender")];
      if (p.reason) bits.push(p.reason);
      if (last) bits.push(`${last.ok ? "repaired itself" : "repair failed"} ${whenFormat.format(new Date(last.at))}`);
      rows.push(portalRow(portalName(p.url), bits.join(" · "), STATE_LABEL[p.state] || p.state,
        last ? `#repair/${encodeURIComponent(p.id)}/${last.n}` : null));
    }
    if (!rows.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "No portals yet. Add one on the Watchtower dashboard.";
      rows.push(empty);
    }
  } else {
    for (const portal of portals) {
      rows.push(portalRow(portal.name, [portal.country, portal.detail].filter(Boolean).join(" · "), portal.status, null));
    }
  }
  byId("portal-list").replaceChildren(...rows);
  byId("count-portals").textContent = live ? livePortals.length : portals.length;
}

// --- Before vs after: what a portal changed, and how Watchtower followed --------------------------
function setRepairLayout() {
  const changed = currentView !== undefined && currentView !== "repair";
  currentView = "repair";
  byId("overview").hidden = true;
  byId("portals").hidden = true;
  byId("tender-section").hidden = true;
  byId("repair-view").hidden = false;
  byId("location").textContent = "Before vs after";
  document.querySelectorAll("nav a").forEach((link) => {
    if (link.hash === "#portals") link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (changed) {
    window.scrollTo(0, 0);
    byId("main").focus({ preventScroll: true });
  }
}

function clearCompare(message) {
  byId("repair-summary").textContent = message;
  byId("frame-before").removeAttribute("src");
  byId("frame-after").removeAttribute("src");
  byId("cap-before").textContent = "";
  byId("cap-after").textContent = "";
  byId("repair-diff").replaceChildren();
  byId("repair-pick").closest(".filters").hidden = true;
}

function reads(side) {
  if (side.source === "json_api") return "reads a JSON feed (not drawn on the page)";
  return `reads ${plural(side.items, "tender row")}`;
}

async function showRepairView(rest) {
  setRepairLayout();
  const [pidRaw, nRaw] = rest.split("/");
  const pid = decodeURIComponent(pidRaw || "");
  let portal;
  let repairs;
  try {
    [portal, repairs] = await Promise.all([getJson(`/portals/${encodeURIComponent(pid)}`), getJson(`/portals/${encodeURIComponent(pid)}/repairs`)]);
  } catch {
    byId("repair-portal").textContent = "";
    clearCompare("This view needs Watchtower: start the server and open Briefly from it.");
    return;
  }
  byId("repair-portal").textContent = portalName(portal.url);
  if (!repairs.length) {
    clearCompare("This portal has not needed a repair yet. Break it in the Chaos Lab to see one here.");
    return;
  }
  const pick = byId("repair-pick");
  pick.replaceChildren(...repairs.map((r) => new Option(`Repair #${r.n} · ${whenFormat.format(new Date(r.at))} · ${r.ok ? "healed" : "failed"}`, r.n)));
  const r = repairs.find((x) => x.n === Number(nRaw)) || repairs[0];
  pick.value = String(r.n);
  pick.closest(".filters").hidden = repairs.length < 2;
  pick.onchange = () => { location.hash = `repair/${encodeURIComponent(pid)}/${pick.value}`; };

  const when = whenFormat.format(new Date(r.at));
  byId("repair-summary").textContent = r.ok
    ? `On ${when}, this portal changed how its page is built. Watchtower noticed, kept serving the last correct tenders, and repaired itself in ${plural(r.attempts, "attempt")}.`
    : `On ${when}, this portal changed and Watchtower could not repair it automatically. A person should take a look.`;
  byId("cap-before").textContent = `· old recipe ${reads(r.before)} · ${plural(r.before.changed, "part")} changed or gone`;
  byId("cap-after").textContent = r.ok
    ? `· new recipe ${reads(r.after)} · ${plural(r.after.changed, "part")} new or changed`
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

function connectLive() {
  if (!("EventSource" in window)) return;
  const es = new EventSource("/events");
  let timer;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      await loadPortals();
      if (currentView === "repair") showRepairView(location.hash.slice("#repair/".length));
    }, 500);
  };
  for (const kind of ["compare", "state"]) es.addEventListener(kind, refresh);
}

function findTender() {
  if (currentView === "tenders") {
    search.focus();
    return;
  }
  window.addEventListener("hashchange", () => search.focus(), { once: true });
  location.hash = "tenders";
}

for (const name of [...new Set(tenders.map(tender => tender.country))].sort()) {
  country.add(new Option(name, name));
}
byId("count-tenders").textContent = tenders.length;
byId("count-portals").textContent = portals.length;
byId("count-saved").textContent = saved.size;
search.addEventListener("input", renderTenders);
country.addEventListener("change", renderTenders);
byId("search-shortcut").addEventListener("click", findTender);
byId("close-detail").addEventListener("click", () => dialog.close());
detailSave.addEventListener("click", () => toggleSaved(activeTender, detailSave));
dialog.addEventListener("close", () => {
  if (currentView === "saved" && !saved.has(activeTender.id)) {
    renderTenders();
    (tenderList.querySelector(".open-tender") ?? search).focus();
  }
});
document.addEventListener("keydown", event => {
  if (event.altKey && !event.ctrlKey && !event.metaKey && event.code === "Slash" && !dialog.open) {
    event.preventDefault();
    findTender();
  }
});
window.addEventListener("hashchange", renderView);
renderView();
renderPortals();
loadPortals().then(() => { if (Array.isArray(livePortals)) connectLive(); });
