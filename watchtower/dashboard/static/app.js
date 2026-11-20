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
    for (const k of ["state", "run", "log", "judgment", "recipe", "verify", "page", "explore", "cost"])
      es.addEventListener(k, (m) => onEvent(JSON.parse(m.data)));
    return es;
  }

  function morning() {
    $("#add-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = e.target.url, btn = e.target.querySelector("button");
      btn.disabled = true;
      try { const { id } = await api("POST", "/portals", { url: input.value.trim() }); input.value = ""; refreshCard(id); }
      catch (err) { alert(err.message); } finally { btn.disabled = false; }
    });
    document.addEventListener("click", async (e) => {
      const t = e.target;
      if (t.dataset.refresh) { t.disabled = true; await api("POST", `/portals/${t.dataset.refresh}/refresh`).catch(alert); scheduleCard(t.dataset.refresh); }
      if (t.dataset.break) { t.disabled = true; const r = await api("POST", `/portals/${t.dataset.break}/break`).catch(alert); if (r) t.textContent = `Broke: ${r.mutation}`; setTimeout(() => { t.disabled = false; t.textContent = "Break it"; }, 4000); }
      if (t.dataset.delete && confirm("Remove this portal and its tenders?")) {
        await api("DELETE", `/portals/${t.dataset.delete}`).catch(alert); document.getElementById(`card-${t.dataset.delete}`)?.remove(); recount();
      }
    });
    connect((ev) => {
      if (ev.portal_id && ["state", "run"].includes(ev.kind)) scheduleCard(ev.portal_id);
      feed(ev);
    });
    setInterval(() => document.querySelectorAll(".card").forEach((c) => scheduleCard(c.dataset.id)), 60000);
  }

  return { api, connect, morning, refreshCard, $ };
})();
