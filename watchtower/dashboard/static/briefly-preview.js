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
for (const portal of portals) {
  const row = document.createElement("li");
  row.className = "portal-item";
  const text = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = portal.name;
  const detail = document.createElement("p");
  detail.textContent = [portal.country, portal.detail].filter(Boolean).join(" · ");
  text.append(name, detail);
  const status = document.createElement("span");
  status.className = "tag";
  status.dataset.status = portal.status;
  status.textContent = portal.status;
  row.append(text, status);
  byId("portal-list").append(row);
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
