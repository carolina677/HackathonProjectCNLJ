"""Per-portal orchestration and the portal state machine (spec §5.2)."""

from . import events
from .db import Repo
from .models import PortalState, RunKind, RunResult

ALLOWED = {
    PortalState.integrating: {PortalState.serving, PortalState.failed, PortalState.integrating},
    PortalState.serving: {PortalState.healing, PortalState.serving, PortalState.failed},
    PortalState.healing: {PortalState.serving, PortalState.failed, PortalState.healing},
    PortalState.failed: {PortalState.integrating, PortalState.failed, PortalState.serving},
}


class Pipeline:
    def __init__(self, repo: Repo):
        self.repo = repo
        self._followups: dict[str, RunKind] = {}

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

    def schedule_followup(self, pid: str, kind: RunKind) -> None:
        self._followups[pid] = kind

    def pop_followup(self, pid: str) -> RunKind | None:
        return self._followups.pop(pid, None)

    async def run(self, pid: str, run_id: str, kind: RunKind) -> RunResult:
        p = await self.repo.get_portal(pid)
        if p is None:
            return RunResult.failed
        if kind == RunKind.repair:
            return await self.repair(pid, run_id)
        if p.recipe is None:
            return await self.attach(pid, run_id)
        return await self.read(pid, run_id)

    async def attach(self, pid: str, run_id: str) -> RunResult:
        raise NotImplementedError

    async def read(self, pid: str, run_id: str) -> RunResult:
        raise NotImplementedError

    async def repair(self, pid: str, run_id: str) -> RunResult:
        raise NotImplementedError


def build_pipeline(repo: Repo, settings, limiter, is_cancelled) -> Pipeline:
    """Factory used by the app; Task 12 swaps in the full agent pipeline."""
    return Pipeline(repo)
