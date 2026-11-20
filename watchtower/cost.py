"""Model spend per portal, for the cost meter and the README's cost-per-portal figure."""

from collections import defaultdict
from typing import Callable

from . import events

JEV_USD_PER_MTOK = 0.042  # docs: $0.042 per 1M input tokens, output free


def _empty() -> dict[str, float]:
    return {"jev_usd": 0.0, "llm_usd": 0.0, "jev_calls": 0, "llm_calls": 0, "tokens": 0}


_ledger: dict[str, dict[str, float]] = defaultdict(_empty)
_persister: Callable[[str, dict[str, float]], None] | None = None


def set_persister(fn: Callable[[str, dict[str, float]], None] | None) -> None:
    global _persister
    _persister = fn


def clear_persister() -> None:
    global _persister
    _persister = None


def reset() -> None:
    _ledger.clear()


def load(rows: dict[str, dict[str, float]]) -> None:
    for pid, row in rows.items():
        _ledger[pid] = {**_empty(), **row}


def forget(portal_id: str) -> None:
    _ledger.pop(portal_id, None)


def record(portal_id: str | None, provider: str, input_tokens: int, output_tokens: int, usd: float) -> None:
    key = portal_id or "_global"
    row = _ledger[key]
    row[f"{provider}_usd"] += usd
    row[f"{provider}_calls"] += 1
    row["tokens"] += input_tokens + output_tokens
    if _persister is not None:
        try:
            _persister(key, dict(row))
        except Exception:
            pass
    if portal_id:
        events.emit(portal_id, "cost", summary(portal_id))


def summary(portal_id: str) -> dict:
    row = _ledger.get(portal_id) or _empty()
    return {**row, "total_usd": row["jev_usd"] + row["llm_usd"]}


def per_portal_average() -> float:
    rows = [summary(p) for p in _ledger if p != "_global"]
    return sum(r["total_usd"] for r in rows) / len(rows) if rows else 0.0


def projected(portals: int = 4500) -> float:
    return per_portal_average() * portals
