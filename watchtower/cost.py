"""Model spend per portal, for the cost meter and the README's cost-per-portal figure."""

from collections import defaultdict

from . import events

JEV_USD_PER_MTOK = 0.042  # docs: $0.042 per 1M input tokens, output free

_ledger: dict[str, dict[str, float]] = defaultdict(lambda: {"jev_usd": 0.0, "llm_usd": 0.0, "jev_calls": 0,
                                                             "llm_calls": 0, "tokens": 0})


def reset() -> None:
    _ledger.clear()


def record(portal_id: str | None, provider: str, input_tokens: int, output_tokens: int, usd: float) -> None:
    row = _ledger[portal_id or "_global"]
    row[f"{provider}_usd"] += usd
    row[f"{provider}_calls"] += 1
    row["tokens"] += input_tokens + output_tokens
    if portal_id:
        events.emit(portal_id, "cost", summary(portal_id))


def summary(portal_id: str) -> dict:
    row = _ledger.get(portal_id) or {"jev_usd": 0.0, "llm_usd": 0.0, "jev_calls": 0, "llm_calls": 0, "tokens": 0}
    return {**row, "total_usd": row["jev_usd"] + row["llm_usd"]}


def per_portal_average() -> float:
    rows = [summary(p) for p in _ledger if p != "_global"]
    return sum(r["total_usd"] for r in rows) / len(rows) if rows else 0.0


def projected(portals: int = 4500) -> float:
    return per_portal_average() * portals
