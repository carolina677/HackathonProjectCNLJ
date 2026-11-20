"""Per-portal orchestration and the portal state machine (spec §5.2)."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from . import events
from .brain.explore import explore
from .brain.fingerprint import fingerprint
from .brain.heal import heal
from .brain.health import fill_rates, health_check, pick_probe, pulse_health, spot_check
from .brain.induce import induce_recipe
from .brain.verify import VerifyReport, verify_sample
from .db import Repo
from .documents import fetch_documents
from .fetch import Fetcher, PortalCancelled
from .judge.jev import Jev
from .judge.llm import LLM, LLMUnavailable
from .models import OPTIONAL_FIELDS, REQUIRED_FIELDS, Document, PortalState, RunKind, RunResult, Tender
from .recipe.dsl import Recipe
from .recipe.engine import list_complete, run_recipe

log = logging.getLogger("watchtower.pipeline")

RETRY_DELAYS = (60.0, 300.0)
MAX_TRANSIENT = 3
SMALL_PORTAL_LIMIT = 50
PROGRESSIVE_SERVE_AT = 20

BLOCK_REASONS = {
    "login": "Portal requires a login",
    "captcha": "Portal shows a captcha and documents are not reachable without it",
}

ALLOWED = {
    PortalState.integrating: {PortalState.serving, PortalState.failed, PortalState.integrating},
    PortalState.serving: {PortalState.healing, PortalState.serving, PortalState.failed},
    PortalState.healing: {PortalState.serving, PortalState.failed, PortalState.healing},
    PortalState.failed: {PortalState.integrating, PortalState.failed, PortalState.serving, PortalState.healing},
}


def to_tender(raw: dict, documents: list[Document], now: datetime, mask: set[str] = frozenset()) -> Tender | None:
    """Engine output → contract Tender. Values that fail validation become null (required) or vanish (optional)."""
    data = {k: v for k, v in raw.items() if not k.startswith("_")}
    data.setdefault("url", raw.get("_base"))
    for k in mask:
        if k in OPTIONAL_FIELDS:
            data.pop(k, None)
        elif k not in ("id", "url", "status", "documents", "lastChangedAt"):
            data[k] = None
    for k in REQUIRED_FIELDS:
        data.setdefault(k, None)
    data["status"] = data["status"] or "open"
    data["documents"], data["lastChangedAt"] = documents, now
    for _ in range(3):
        try:
            return Tender(**data)
        except ValidationError as e:
            bad = {str(err["loc"][0]) for err in e.errors() if err["loc"]}
            if bad & {"id", "url"} or not bad:
                return None
            for k in bad:
                if k in OPTIONAL_FIELDS:
                    data.pop(k, None)
                elif k == "status":
                    data[k] = "open"
                else:
                    data[k] = None
    return None


def make_judges(settings) -> tuple[Jev, LLM]:
    llm = LLM(settings)
    return Jev(settings, llm=llm), llm


class Pipeline:
    def __init__(self, repo: Repo, settings=None, limiter=None, is_cancelled=None, jev=None, llm=None):
        self.repo, self.settings, self.limiter = repo, settings, limiter
        self.is_cancelled = is_cancelled or (lambda pid: False)
        self.jev, self.llm = jev, llm
        self.read_jev = jev.without_llm() if hasattr(jev, "without_llm") else jev
        self._followups: dict[str, tuple[RunKind, float]] = {}
        self._outages: dict[str, int] = {}
        self._probe_turn: dict[str, int] = {}
        self._docs: dict[tuple[str, str], tuple[tuple[str, ...], list[Document]]] = {}

    def forget(self, pid: str) -> None:
        self._followups.pop(pid, None)
        self._outages.pop(pid, None)
        self._probe_turn.pop(pid, None)
        for key in [k for k in self._docs if k[0] == pid]:
            self._docs.pop(key, None)

    @staticmethod
    def check_transition(frm: PortalState, to: PortalState) -> None:
        if to not in ALLOWED[frm]:
            raise ValueError(f"illegal transition {frm} -> {to}")

    async def transition(self, pid: str, to: PortalState, reason: str | None = None) -> None:
        p = await self.repo.get_portal(pid)
        if p is None:
            return
        self.check_transition(p.state, to)
        await self.repo.set_state(pid, to, reason)
        if p.state != to or p.reason != reason:
            events.emit(pid, "state", {"from": str(p.state), "to": str(to), "reason": reason})

    def schedule_followup(self, pid: str, kind: RunKind, delay: float = 0.0) -> None:
        self._followups[pid] = (kind, delay)

    def pop_followup(self, pid: str) -> tuple[RunKind, float] | None:
        return self._followups.pop(pid, None)

    def fetcher(self, pid: str) -> Fetcher:
        f = Fetcher(pid, self.limiter, cancelled=lambda: self.is_cancelled(pid),
                    snapshot_dir=self.settings.data_dir if self.settings else None)
        if isinstance(self.jev, Jev) and self.jev.client is not None:
            from .dashboard.vision import look

            f.on_screenshot = lambda png, url: look(self.jev, png, pid, url)
        return f

    async def run(self, pid: str, run_id: str, kind: RunKind) -> RunResult:
        p = await self.repo.get_portal(pid)
        if p is None:
            return RunResult.failed
        try:
            if kind == RunKind.repair:
                return await self.repair(pid, run_id)
            if p.recipe is None:
                return await self.attach(pid, run_id)
            return await self.read(pid, run_id)
        except PortalCancelled:
            return RunResult.failed
        except Exception as e:
            log.exception("run %s crashed", run_id)
            cur = await self.repo.get_portal(pid)
            if cur and cur.state in (PortalState.integrating, PortalState.healing):
                await self.transition(pid, PortalState.failed, f"Internal error while working on the portal: {e}")
            raise

    # --- attach ----------------------------------------------------------
    async def _try(self, recipe: Recipe, fetcher, pid: str, limit: int = 10, expect_docs: bool = False):
        errors: list[str] = []
        pages: dict[str, str] = {}
        try:
            sample = await run_recipe(recipe, fetcher, limit=limit, errors=errors, pages=pages)
        except PortalCancelled:
            raise
        except Exception as e:
            sample, errors = [], [f"recipe crashed: {e}"]
        v = await verify_sample(sample, pages, self.jev, country=recipe.country or "DE", portal_id=pid,
                                expect_docs=expect_docs)
        return sample, errors, v

    async def attach(self, pid: str, run_id: str) -> RunResult:
        p = await self.repo.get_portal(pid)
        await self.transition(pid, PortalState.integrating, None)
        fetcher = self.fetcher(pid)
        events.emit(pid, "log", {"msg": f"Attaching {p.url}"})
        recipe: Recipe | None = None
        v: VerifyReport | None = None
        sample: list[dict] = []
        try:
            start = await fetcher.open(p.url)
        except PortalCancelled:
            raise
        except Exception as e:
            await self.transition(pid, PortalState.failed, f"Could not reach the portal: {e}")
            return RunResult.failed
        known = fingerprint(start)
        if known is not None:
            events.emit(pid, "log", {"msg": f"Recognised platform {known.platform}; using its adapter"})
            sample, _, v = await self._try(known, fetcher, pid)
            recipe = known if v.ok and sample else None
        ex = None
        if recipe is None:
            ex = await explore(p.url, fetcher, self.jev, portal_id=pid)
            if ex.blocked:
                await self.transition(pid, PortalState.failed, BLOCK_REASONS[ex.blocked])
                return RunResult.failed
            if ex.list_page is None:
                await self.transition(pid, PortalState.failed,
                                      f"Could not find the tender list: {ex.error or 'no list page'}")
                return RunResult.failed
            feedback: list[str] | None = None
            for attempt in range(3):
                try:
                    cand = await induce_recipe(ex, self.llm, feedback=feedback, portal_id=pid)
                except LLMUnavailable as e:
                    await self.transition(pid, PortalState.failed, f"No LLM available to learn the portal: {e}")
                    return RunResult.failed
                sample, errors, v = await self._try(cand, fetcher, pid, expect_docs=ex.docs_reachable)
                events.emit(pid, "log", {"msg": f"Recipe attempt {attempt + 1}: {len(sample)} tenders, "
                                                f"{'verified' if v.ok else 'rejected'}"})
                if v.ok and sample:
                    recipe = cand
                    break
                feedback = (errors[:5] + v.feedback)[:15] or ["The recipe returned nothing usable."]
        if recipe is None:
            why = "; ".join((v.problems if v else [])[:3]) or "extracted values did not match the pages"
            await self.transition(pid, PortalState.failed, f"Could not learn a reliable recipe: {why}")
            return RunResult.failed
        if await self.repo.is_deleted(pid):
            return RunResult.failed
        snap = fetcher.snapshot(ex.list_page if ex else start) or None
        mask = v.mask if v else []
        await self.repo.set_recipe(pid, json.loads(recipe.model_dump_json()), snapshot_ref=snap,
                                   field_mask=mask, country=recipe.country)
        return await self._initial_read(pid, recipe, set(mask), fetcher)

    async def _initial_read(self, pid: str, recipe: Recipe, mask: set[str], fetcher: Fetcher) -> RunResult:
        """Read full tenders and documents before moving out of integrating (progressively for huge portals)."""
        from .brain.health import Health

        errors: list[str] = []
        pages: dict[str, str] = {}
        now = datetime.now(timezone.utc)
        tenders: dict[str, Tender] = {}
        doc_counts: dict[str, int] = {}
        doc_errors: list[str] = []
        list_health: list[Health] = []
        list_total = 0
        progressive_served = False

        async def on_list(items, errs):
            nonlocal list_total
            list_total = len(items)
            h = health_check(0, items, errs)
            list_health.append(h)
            if h.ok:
                events.emit(pid, "log", {"msg": f"List read: {len(items)} tenders; fetching details and documents"})
            return h.ok

        async def on_tender(r):
            nonlocal progressive_served
            docs = await self._documents(pid, r, fetcher, doc_errors)
            doc_counts[str(r["id"])] = len(docs)
            t = to_tender(r, docs, now, mask)
            if t is None:
                return
            tenders[t.id] = t
            if list_total > SMALL_PORTAL_LIMIT and not await self.repo.is_deleted(pid):
                await self.repo.upsert_tenders(pid, [t], now)
                if not progressive_served and len(tenders) >= PROGRESSIVE_SERVE_AT:
                    await self.repo.mark_success(pid, len(tenders), now)
                    await self.transition(pid, PortalState.serving, None)
                    progressive_served = True

        raw = await run_recipe(recipe, fetcher, errors=errors, mask=mask, pages=pages,
                               on_list=on_list, on_tender=on_tender)
        if await self.repo.is_deleted(pid):
            return RunResult.failed
        health = list_health[0] if list_health and not list_health[0].ok else \
            health_check(0, raw, errors, doc_counts=doc_counts, doc_errors=doc_errors)
        if not health.ok and not progressive_served:
            await self.transition(pid, PortalState.failed, health.reason)
            return RunResult.failed
        if not tenders:
            if not progressive_served:
                await self.transition(pid, PortalState.failed, "Could not extract valid tenders from the portal.")
            return RunResult.failed
        if list_total <= SMALL_PORTAL_LIMIT:
            await self.repo.upsert_tenders(pid, list(tenders.values()), now)
        await self.repo.mark_success(pid, len(tenders), now, fill=fill_rates(raw, doc_counts=doc_counts))
        self._outages.pop(pid, None)
        if not progressive_served:
            await self.transition(pid, PortalState.serving, None)
        events.emit(pid, "log", {"msg": f"Serving {len(tenders)} tenders with documents ({fetcher.requests} requests)"})
        return RunResult.ok

    # --- read --------------------------------------------------------------
    async def _documents(self, pid: str, raw: dict, fetcher, doc_errors: list[str] | None = None) -> list[Document]:
        urls = tuple(raw.get("_doc_urls") or ())
        key = (pid, str(raw["id"]))
        hit = self._docs.get(key)
        if hit and hit[0] == urls:
            return hit[1]
        docs = await fetch_documents(pid, str(raw["id"]), list(urls), fetcher, repo=self.repo,
                                     data_dir=self.settings.data_dir, errors=doc_errors) if urls else []
        if docs or not urls:
            self._docs[key] = (urls, docs)
        return docs

    async def read(self, pid: str, run_id: str) -> RunResult:
        """Deterministic: the stored recipe, no model call. Jev spot-checks one tender (no LLM fallback).

        Large portals: the list is checked first; new tenders are served one by one while details and documents
        arrive. Tenders we already serve are only overwritten after the whole read passed its health check."""
        from .brain.health import Health

        p = await self.repo.get_portal(pid)
        recipe = Recipe.model_validate(p.recipe)
        mask = set(p.field_mask or [])
        fetcher = self.fetcher(pid)
        errors: list[str] = []
        pages: dict[str, str] = {}
        known = {t.id for t in await self.repo.list_tenders(pid)}
        now = datetime.now(timezone.utc)
        tenders: dict[str, Tender] = {}
        doc_counts: dict[str, int] = {}
        doc_errors: list[str] = []
        list_health: list[Health] = []

        async def on_list(items, errs):
            h = health_check(p.prev_count or 0, items, errs)
            list_health.append(h)
            if h.ok:
                events.emit(pid, "log", {"msg": f"List read: {len(items)} tenders; fetching details and documents"})
            return h.ok

        async def on_tender(r):
            docs = await self._documents(pid, r, fetcher, doc_errors)
            doc_counts[str(r["id"])] = len(docs)
            t = to_tender(r, docs, now, mask)
            if t is None:
                return
            tenders[t.id] = t
            if t.id not in known and not await self.repo.is_deleted(pid):
                await self.repo.upsert_tenders(pid, [t], now)

        raw = await run_recipe(recipe, fetcher, errors=errors, mask=mask, pages=pages,
                               on_list=on_list, on_tender=on_tender)
        health = list_health[0] if list_health and not list_health[0].ok else \
            health_check(p.prev_count or 0, raw, errors, p.prev_fill or None,
                         doc_counts=doc_counts, doc_errors=doc_errors)
        if health.ok:
            probe = pick_probe(raw, pages, self._probe_turn.get(pid, 0))
            if probe is not None:
                self._probe_turn[pid] = self._probe_turn.get(pid, 0) + 1
                if not await spot_check(probe, pages[probe["id"]], self.read_jev, pid):
                    health = Health(False, f"Spot check: the values of tender {probe['id']} no longer match its page.")
        if await self.repo.is_deleted(pid):
            return RunResult.failed
        if not health.ok:
            events.emit(pid, "log", {"msg": f"Read unhealthy: {health.reason}"})
            await self.transition(pid, PortalState.healing, health.reason)
            if health.transient:
                n = self._outages.get(pid, 0) + 1
                self._outages[pid] = n
                if n < MAX_TRANSIENT:
                    self.schedule_followup(pid, RunKind.read, RETRY_DELAYS[n - 1])
                else:
                    self.schedule_followup(pid, RunKind.repair, 0.0)
            else:
                self._outages.pop(pid, None)
                self.schedule_followup(pid, RunKind.repair, 0.0)
            return RunResult.failed
        self._outages.pop(pid, None)
        changed = await self.repo.upsert_tenders(pid, list(tenders.values()), now)
        # Only a fully read list says a tender is gone; a cut-off or failed page proves nothing.
        closed = await self.repo.mark_missing_closed(pid, set(tenders), now) if list_complete(errors) else 0
        await self.repo.mark_success(pid, len(tenders), now, fill=fill_rates(raw, doc_counts=doc_counts))
        if p.state != PortalState.serving:
            await self.transition(pid, PortalState.serving, None)
        events.emit(pid, "log", {"msg": f"Read {len(tenders)} tenders ({changed} changed, {closed} closed, "
                                        f"{fetcher.requests} requests, no LLM)"})
        return RunResult.ok

    async def pulse(self, pid: str) -> bool:
        """List-only check between full reads: no details, no documents, no Jev, no LLM.

        True means healthy (or nothing to check). False means "read now": the full read decides the state."""
        p = await self.repo.get_portal(pid)
        if p is None or p.state != PortalState.serving or not p.recipe:
            return True
        errors: list[str] = []
        seen: list[dict] = []

        async def on_list(items, errs):
            seen.extend(items)
            return False

        try:
            await run_recipe(Recipe.model_validate(p.recipe), self.fetcher(pid), limit=10, errors=errors,
                             mask=set(p.field_mask or []), on_list=on_list)
        except PortalCancelled:
            return True  # deleted while pulsing
        h = pulse_health(seen, errors, p.prev_fill or None)
        if not h.ok:
            events.emit(pid, "log", {"msg": f"Pulse: {h.reason} Reading now."})
        return h.ok

    # --- repair ------------------------------------------------------------
    async def repair(self, pid: str, run_id: str) -> RunResult:
        from .brain.health import Health

        self._followups.pop(pid, None)
        p = await self.repo.get_portal(pid)
        if p.recipe is None:
            return await self.attach(pid, run_id)
        if p.state != PortalState.healing:
            await self.transition(pid, PortalState.healing, p.reason or "repair requested")
            p = await self.repo.get_portal(pid)
        old = Recipe.model_validate(p.recipe)
        snap_html = None
        if p.snapshot_ref and self.settings:
            f = Path(self.settings.data_dir) / p.snapshot_ref
            snap_html = f.read_text() if f.exists() else None
        fetcher = self.fetcher(pid)
        new, rep = await heal(pid, p.url, old, snap_html, fetcher, self.jev, self.llm,
                              reason=p.reason or "the portal changed", mask=set(p.field_mask or []))
        if await self.repo.is_deleted(pid):
            return RunResult.failed
        if new is None:
            if rep.blocked:
                why = BLOCK_REASONS[rep.blocked]
            else:
                why = f"Could not repair automatically after {rep.attempts} attempts ({p.reason})"
                if rep.exploration_error:
                    why += f": {rep.exploration_error}"
            self._record_repair(pid, p, old, None, rep, ok=False, reason=why,
                                after_ref=fetcher.snapshot(rep.list_page) if rep.list_page else None)
            await self.transition(pid, PortalState.failed, why)
            return RunResult.failed
        page = rep.list_page or await fetcher.open(p.url)
        after_ref = fetcher.snapshot(page) or None
        self._record_repair(pid, p, old, new, rep, ok=True, reason=p.reason, after_ref=after_ref)
        new_mask = rep.verify.mask if rep.verify else []
        await self.repo.set_recipe(pid, json.loads(new.model_dump_json()), snapshot_ref=after_ref,
                                   field_mask=new_mask, country=new.country)
        events.emit(pid, "log", {"msg": "Repaired: " + ("; ".join(rep.diff[:4]) or "recipe re-learned")})

        # Keep serving old tenders in `healing` until fresh tenders & documents are ready (unless huge portal).
        errors: list[str] = []
        pages: dict[str, str] = {}
        now = datetime.now(timezone.utc)
        tenders: dict[str, Tender] = {}
        doc_counts: dict[str, int] = {}
        doc_errors: list[str] = []
        list_health: list[Health] = []
        large_portal = False

        async def on_list(items, errs):
            nonlocal large_portal
            h = health_check(0, items, errs)
            list_health.append(h)
            if not h.ok:
                return False
            if len(items) > SMALL_PORTAL_LIMIT:
                large_portal = True
                return False
            return True

        async def on_tender(r):
            docs = await self._documents(pid, r, fetcher, doc_errors)
            doc_counts[str(r["id"])] = len(docs)
            t = to_tender(r, docs, now, set(new_mask))
            if t is not None:
                tenders[t.id] = t

        raw = await run_recipe(new, fetcher, errors=errors, mask=set(new_mask), pages=pages,
                               on_list=on_list, on_tender=on_tender)
        if await self.repo.is_deleted(pid):
            return RunResult.failed
        if large_portal:
            self._outages.pop(pid, None)
            await self.transition(pid, PortalState.serving, None)
            self.schedule_followup(pid, RunKind.read, 0.0)
            return RunResult.ok
        health = list_health[0] if list_health and not list_health[0].ok else \
            health_check(0, raw, errors, doc_counts=doc_counts, doc_errors=doc_errors)
        if health.ok and tenders:
            await self.repo.upsert_tenders(pid, list(tenders.values()), now)
            if list_complete(errors):
                await self.repo.mark_missing_closed(pid, set(tenders), now)
            await self.repo.mark_success(pid, len(tenders), now, fill=fill_rates(raw, doc_counts=doc_counts))
            self._outages.pop(pid, None)
            await self.transition(pid, PortalState.serving, None)
            return RunResult.ok
        self.schedule_followup(pid, RunKind.read, 60.0)
        return RunResult.failed

    def _record_repair(self, pid: str, p, old: Recipe, new: Recipe | None, rep, *, ok: bool, reason: str | None,
                       after_ref: str | None) -> None:
        """Keep the before/after pages of this repair for the Agent Theater (display only, never raises)."""
        if not self.settings:
            return
        from . import repairs

        row = repairs.record(
            Path(self.settings.data_dir), pid, ok=ok, reason=reason, diff=rep.diff, attempts=rep.attempts,
            before_ref=p.snapshot_ref, before_url=old.list_url, before_recipe=json.loads(old.model_dump_json()),
            after_ref=after_ref, after_url=getattr(rep.list_page, "final_url", None) or p.url,
            after_recipe=json.loads(new.model_dump_json()) if new else None)
        if row:
            events.emit(pid, "compare", {"n": row["n"], "ok": ok})


def build_pipeline(repo: Repo, settings, limiter, is_cancelled) -> Pipeline:
    jev, llm = make_judges(settings)
    return Pipeline(repo, settings, limiter, is_cancelled, jev, llm)
