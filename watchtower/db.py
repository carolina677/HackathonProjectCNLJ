"""SQLite persistence. One file under data_dir; everything the API serves comes from here."""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .models import PortalOut, PortalState, RunKind, RunOut, RunResult, Tender, TenderStatus, tender_json
from .urls import normalize_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS portals (
  id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, state TEXT NOT NULL, reason TEXT,
  last_success_at TEXT, recipe TEXT, field_mask TEXT, snapshot_ref TEXT, country TEXT,
  prev_count INTEGER DEFAULT 0, prev_fill TEXT, deleted INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenders (
  portal_id TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, content_hash TEXT NOT NULL,
  last_changed_at TEXT NOT NULL, PRIMARY KEY (portal_id, id)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, portal_id TEXT NOT NULL, kind TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, result TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, portal_id TEXT, tender_id TEXT, file_name TEXT, size_bytes INTEGER,
  sha256 TEXT, stored_path TEXT
);
CREATE TABLE IF NOT EXISTS costs (
  portal_id TEXT PRIMARY KEY,
  jev_usd REAL DEFAULT 0.0,
  llm_usd REAL DEFAULT 0.0,
  jev_calls INTEGER DEFAULT 0,
  llm_calls INTEGER DEFAULT 0,
  tokens INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_portal ON runs(portal_id, started_at);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


@dataclass
class PortalRow:
    id: str
    url: str
    state: PortalState
    reason: str | None
    tenderCount: int
    lastSuccessAt: datetime | None
    recipe: dict | None = None
    field_mask: list[str] = field(default_factory=list)
    snapshot_ref: str | None = None
    country: str | None = None
    prev_count: int = 0
    prev_fill: dict[str, float] = field(default_factory=dict)
    deleted: bool = False

    def out(self) -> PortalOut:
        return PortalOut(id=self.id, url=self.url, state=self.state, reason=self.reason,
                         tenderCount=self.tenderCount, lastSuccessAt=self.lastSuccessAt)


@dataclass
class DocRow:
    id: str
    file_name: str
    size_bytes: int
    sha256: str
    stored_path: str


def _content_hash(t: Tender) -> str:
    d = tender_json(t)
    d.pop("lastChangedAt", None)
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


class Repo:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "watchtower.db"
        self.db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.executescript(SCHEMA)
        cur = await self.db.execute("PRAGMA table_info(portals)")
        cols = {r["name"] for r in await cur.fetchall()}
        if "prev_fill" not in cols:
            await self.db.execute("ALTER TABLE portals ADD COLUMN prev_fill TEXT")
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    # --- portals -------------------------------------------------------
    async def create_portal(self, url: str) -> str:
        nurl = normalize_url(url)
        cur = await self.db.execute("SELECT id, deleted FROM portals WHERE url=?", (nurl,))
        row = await cur.fetchone()
        if row:
            if row["deleted"]:
                await self.db.execute(
                    "UPDATE portals SET deleted=0, state=?, reason=NULL, recipe=NULL, "
                    "last_success_at=NULL, prev_count=0, prev_fill=NULL WHERE id=?",
                    (PortalState.integrating, row["id"]))
                await self.db.execute("DELETE FROM runs WHERE portal_id=?", (row["id"],))
                await self.db.execute("DELETE FROM documents WHERE portal_id=?", (row["id"],))
                await self.db.execute("DELETE FROM costs WHERE portal_id=?", (row["id"],))
                await self.db.commit()
            return row["id"]
        pid = _new_id()
        await self.db.execute(
            "INSERT INTO portals (id, url, state, created_at) VALUES (?,?,?,?)",
            (pid, nurl, PortalState.integrating, now_utc().isoformat()))
        await self.db.commit()
        return pid

    async def _row(self, r: Any) -> PortalRow:
        cur = await self.db.execute("SELECT COUNT(*) FROM tenders WHERE portal_id=?", (r["id"],))
        count = (await cur.fetchone())[0]
        return PortalRow(
            id=r["id"], url=r["url"], state=PortalState(r["state"]), reason=r["reason"],
            tenderCount=count, lastSuccessAt=_dt(r["last_success_at"]),
            recipe=json.loads(r["recipe"]) if r["recipe"] else None,
            field_mask=json.loads(r["field_mask"]) if r["field_mask"] else [],
            snapshot_ref=r["snapshot_ref"], country=r["country"], prev_count=r["prev_count"] or 0,
            prev_fill=json.loads(r["prev_fill"]) if r["prev_fill"] else {},
            deleted=bool(r["deleted"]))

    async def get_portal(self, pid: str) -> PortalRow | None:
        cur = await self.db.execute("SELECT * FROM portals WHERE id=? AND deleted=0", (pid,))
        r = await cur.fetchone()
        return await self._row(r) if r else None

    async def list_portals(self) -> list[PortalRow]:
        cur = await self.db.execute("SELECT * FROM portals WHERE deleted=0 ORDER BY created_at")
        return [await self._row(r) for r in await cur.fetchall()]

    async def is_deleted(self, pid: str) -> bool:
        cur = await self.db.execute("SELECT deleted FROM portals WHERE id=?", (pid,))
        r = await cur.fetchone()
        return r is None or bool(r["deleted"])

    async def delete_portal(self, pid: str) -> None:
        await self.db.execute("UPDATE portals SET deleted=1 WHERE id=?", (pid,))
        await self.db.execute("DELETE FROM tenders WHERE portal_id=?", (pid,))
        await self.db.execute("DELETE FROM documents WHERE portal_id=?", (pid,))
        await self.db.execute("DELETE FROM costs WHERE portal_id=?", (pid,))
        await self.db.commit()

    async def set_state(self, pid: str, state: PortalState, reason: str | None) -> None:
        await self.db.execute("UPDATE portals SET state=?, reason=? WHERE id=?", (state, reason, pid))
        await self.db.commit()

    async def set_recipe(self, pid: str, recipe: dict | None, snapshot_ref: str | None = None,
                         field_mask: list[str] | None = None, country: str | None = None) -> None:
        await self.db.execute(
            "UPDATE portals SET recipe=?, snapshot_ref=COALESCE(?, snapshot_ref), "
            "field_mask=COALESCE(?, field_mask), country=COALESCE(?, country), prev_fill=NULL WHERE id=?",
            (json.dumps(recipe) if recipe is not None else None, snapshot_ref,
             json.dumps(field_mask) if field_mask is not None else None, country, pid))
        await self.db.commit()

    async def mark_success(self, pid: str, count: int, when: datetime | None = None,
                           fill: dict[str, float] | None = None) -> None:
        if fill is None:
            await self.db.execute("UPDATE portals SET last_success_at=?, prev_count=? WHERE id=?",
                                  ((when or now_utc()).isoformat(), count, pid))
        else:
            await self.db.execute("UPDATE portals SET last_success_at=?, prev_count=?, prev_fill=? WHERE id=?",
                                  ((when or now_utc()).isoformat(), count, json.dumps(fill), pid))
        await self.db.commit()

    # --- tenders -------------------------------------------------------
    async def upsert_tenders(self, pid: str, tenders: list[Tender], now: datetime | None = None) -> int:
        changed = 0
        for t in tenders:
            h = _content_hash(t)
            cur = await self.db.execute(
                "SELECT content_hash FROM tenders WHERE portal_id=? AND id=?", (pid, t.id))
            old = await cur.fetchone()
            if old and old["content_hash"] == h:
                continue
            stamp = now or t.lastChangedAt
            stored = t.model_copy(update={"lastChangedAt": stamp})
            await self.db.execute(
                "INSERT INTO tenders (portal_id, id, data, content_hash, last_changed_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(portal_id, id) DO UPDATE SET data=excluded.data, "
                "content_hash=excluded.content_hash, last_changed_at=excluded.last_changed_at",
                (pid, t.id, stored.model_dump_json(), h, stamp.isoformat()))
            changed += 1
        await self.db.commit()
        return changed

    async def activity_7d(self) -> dict[str, Any]:
        """Return 7-day tender update counts ending today (UTC) for the overview chart."""
        today = now_utc().date()
        days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
        cutoff = f"{days[0].isoformat()}T00:00:00"
        cur = await self.db.execute(
            "SELECT substr(t.last_changed_at, 1, 10) AS day, COUNT(*) AS cnt "
            "FROM tenders t JOIN portals p ON p.id = t.portal_id "
            "WHERE p.deleted = 0 AND t.last_changed_at >= ? "
            "GROUP BY day",
            (cutoff,),
        )
        counts = {r["day"]: int(r["cnt"] or 0) for r in await cur.fetchall()}
        series = [
            {
                "date": d.isoformat(),
                "label": d.strftime("%a")[0],
                "weekday": d.strftime("%a"),
                "count": counts.get(d.isoformat(), 0),
            }
            for d in days
        ]
        return {"total": sum(item["count"] for item in series), "days": series}

    async def list_tenders(self, pid: str, changed_since: datetime | None = None) -> list[Tender]:
        if changed_since is not None and changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        cur = await self.db.execute(
            "SELECT data, last_changed_at FROM tenders WHERE portal_id=? ORDER BY id", (pid,))
        out = []
        for r in await cur.fetchall():
            t = Tender.model_validate_json(r["data"])
            if changed_since is None or t.lastChangedAt > changed_since:
                out.append(t)
        return out

    async def mark_missing_closed(self, pid: str, seen_ids: set[str], now: datetime | None = None) -> int:
        n = 0
        for t in await self.list_tenders(pid):
            if t.id not in seen_ids and t.status == TenderStatus.open:
                await self.upsert_tenders(pid, [t.model_copy(update={"status": TenderStatus.closed})],
                                          now=now or now_utc())
                n += 1
        return n

    async def close_expired(self, now: datetime | None = None) -> int:
        now = now or now_utc()
        n = 0
        cur = await self.db.execute("SELECT DISTINCT portal_id FROM tenders")
        for (pid,) in await cur.fetchall():
            for t in await self.list_tenders(pid):
                if t.status == TenderStatus.open and t.submissionDeadline and t.submissionDeadline < now:
                    await self.upsert_tenders(pid, [t.model_copy(update={"status": TenderStatus.closed})], now=now)
                    n += 1
        return n

    # --- runs ----------------------------------------------------------
    async def start_run(self, pid: str, kind: RunKind | str) -> str:
        rid = _new_id()
        await self.db.execute("INSERT INTO runs (id, portal_id, kind, started_at) VALUES (?,?,?,?)",
                              (rid, pid, str(kind), now_utc().isoformat()))
        await self.db.commit()
        return rid

    async def finish_run(self, rid: str, result: RunResult | str, detail: str | None = None) -> None:
        await self.db.execute("UPDATE runs SET finished_at=?, result=?, detail=? WHERE id=?",
                              (now_utc().isoformat(), str(result), detail, rid))
        await self.db.commit()

    async def running_run(self, pid: str, kind: RunKind | str | None = None) -> str | None:
        q = "SELECT id FROM runs WHERE portal_id=? AND finished_at IS NULL"
        args: tuple = (pid,)
        if kind:
            q += " AND kind=?"
            args += (str(kind),)
        cur = await self.db.execute(q + " ORDER BY started_at DESC LIMIT 1", args)
        r = await cur.fetchone()
        return r["id"] if r else None

    async def list_runs(self, pid: str, limit: int = 200) -> list[RunOut]:
        cur = await self.db.execute(
            "SELECT * FROM runs WHERE portal_id=? ORDER BY started_at DESC LIMIT ?", (pid, limit))
        return [RunOut(id=r["id"], kind=RunKind(r["kind"]), startedAt=_dt(r["started_at"]),
                       finishedAt=_dt(r["finished_at"]),
                       result=RunResult(r["result"]) if r["result"] else None)
                for r in await cur.fetchall()]

    async def run_details(self, pid: str, limit: int = 20) -> list[dict]:
        cur = await self.db.execute(
            "SELECT id, kind, started_at, finished_at, result, detail FROM runs WHERE portal_id=? "
            "ORDER BY started_at DESC LIMIT ?", (pid, limit))
        return [dict(r) for r in await cur.fetchall()]

    async def runs_since(self, since: datetime) -> list[dict]:
        cur = await self.db.execute("SELECT * FROM runs WHERE started_at >= ?", (since.isoformat(),))
        return [dict(r) for r in await cur.fetchall()]

    async def fail_orphaned_runs(self) -> int:
        cur = await self.db.execute(
            "UPDATE runs SET finished_at=?, result=?, detail=? WHERE finished_at IS NULL",
            (now_utc().isoformat(), str(RunResult.failed), "interrupted by restart"))
        await self.db.commit()
        return cur.rowcount or 0

    # --- costs ---------------------------------------------------------
    async def load_costs(self) -> dict[str, dict[str, float]]:
        cur = await self.db.execute(
            "SELECT portal_id, jev_usd, llm_usd, jev_calls, llm_calls, tokens FROM costs")
        return {
            r["portal_id"]: {
                "jev_usd": float(r["jev_usd"] or 0.0),
                "llm_usd": float(r["llm_usd"] or 0.0),
                "jev_calls": int(r["jev_calls"] or 0),
                "llm_calls": int(r["llm_calls"] or 0),
                "tokens": int(r["tokens"] or 0),
            }
            for r in await cur.fetchall()
        }

    async def save_cost(self, portal_id: str, row: dict[str, float]) -> None:
        if self.db is None:
            return
        await self.db.execute(
            "INSERT INTO costs (portal_id, jev_usd, llm_usd, jev_calls, llm_calls, tokens) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(portal_id) DO UPDATE SET jev_usd=excluded.jev_usd, llm_usd=excluded.llm_usd, "
            "jev_calls=excluded.jev_calls, llm_calls=excluded.llm_calls, tokens=excluded.tokens",
            (portal_id, float(row.get("jev_usd", 0.0)), float(row.get("llm_usd", 0.0)),
             int(row.get("jev_calls", 0)), int(row.get("llm_calls", 0)), int(row.get("tokens", 0))))
        await self.db.commit()

    # --- documents -----------------------------------------------------
    async def save_document(self, *, portal_id: str, tender_id: str, file_name: str, size_bytes: int,
                            sha256: str, stored_path: str) -> str:
        cur = await self.db.execute(
            "SELECT id FROM documents WHERE portal_id=? AND tender_id=? AND sha256=? AND file_name=?",
            (portal_id, tender_id, sha256, file_name))
        r = await cur.fetchone()
        if r:
            return r["id"]
        fid = _new_id()
        await self.db.execute(
            "INSERT INTO documents (id, portal_id, tender_id, file_name, size_bytes, sha256, stored_path) "
            "VALUES (?,?,?,?,?,?,?)", (fid, portal_id, tender_id, file_name, size_bytes, sha256, stored_path))
        await self.db.commit()
        return fid

    async def get_document(self, fid: str) -> DocRow | None:
        cur = await self.db.execute("SELECT * FROM documents WHERE id=?", (fid,))
        r = await cur.fetchone()
        return DocRow(r["id"], r["file_name"], r["size_bytes"], r["sha256"], r["stored_path"]) if r else None
