import aiosqlite

from watchtower.db import Repo
from watchtower.models import RunResult


async def test_prev_fill_survives_reopen(tmp_path):
    r = Repo(tmp_path)
    await r.init()
    pid = await r.create_portal("https://p.example/")
    await r.mark_success(pid, 5, fill={"title": 1.0, "buyerName": 0.8})
    await r.close()

    r2 = Repo(tmp_path)
    await r2.init()
    p = await r2.get_portal(pid)
    assert p.prev_fill == {"title": 1.0, "buyerName": 0.8}
    await r2.close()


async def test_old_database_gets_prev_fill_column(tmp_path):
    db_path = tmp_path / "watchtower.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript("""
            CREATE TABLE portals (
              id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, state TEXT NOT NULL, reason TEXT,
              last_success_at TEXT, recipe TEXT, field_mask TEXT, snapshot_ref TEXT, country TEXT,
              prev_count INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0, created_at TEXT NOT NULL
            );
            INSERT INTO portals (id, url, state, created_at) VALUES ('p1', 'https://old.example', 'serving', '2026-11-20T00:00:00+00:00');
        """)
        await db.commit()

    r = Repo(tmp_path)
    await r.init()
    p = await r.get_portal("p1")
    assert p is not None and p.prev_fill == {}
    await r.mark_success("p1", 3, fill={"title": 1.0})
    assert (await r.get_portal("p1")).prev_fill == {"title": 1.0}
    await r.close()


async def test_costs_persist_across_reopen(tmp_path):
    r = Repo(tmp_path)
    await r.init()
    await r.save_cost("p1", {"jev_usd": 0.001, "llm_usd": 0.05, "jev_calls": 3, "llm_calls": 1, "tokens": 5000})
    await r.close()

    r2 = Repo(tmp_path)
    await r2.init()
    loaded = await r2.load_costs()
    assert loaded["p1"]["llm_usd"] == 0.05 and loaded["p1"]["jev_calls"] == 3 and loaded["p1"]["tokens"] == 5000
    await r2.close()


async def test_fail_orphaned_runs(tmp_path):
    r = Repo(tmp_path)
    await r.init()
    pid = await r.create_portal("https://p.example/")
    rid = await r.start_run(pid, "read")
    assert await r.fail_orphaned_runs() == 1
    run = (await r.list_runs(pid))[0]
    assert run.id == rid and run.result == RunResult.failed and run.finishedAt is not None
    await r.close()
