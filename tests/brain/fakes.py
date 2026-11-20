"""A deterministic stand-in for Jev that judges lab pages by their content, like a competent model would."""

import re

from watchtower.judge.jev import Choice


class FakeJev:
    def __init__(self):
        self.calls = 0
        self.client = object()

    async def choice(self, state, instructions, criteria, *, portal_id=None, tag=""):
        self.calls += 1
        text = (state.get("text") or "") if isinstance(state, dict) else ""
        forms = str(state.get("forms", "")) if isinstance(state, dict) else ""
        if "candidates" in state:
            return self._pick_candidate(state, criteria)
        if "password" in forms:
            pick = "login_wall"
        elif "Roboter" in text:
            pick = "captcha"
        elif "Bekanntmachung" in text and "Auftragsunterlagen" in text:
            pick = "notice_summary"
        elif forms and "Suchformular" in text:
            pick = "search_form"
        elif "Vergabenummer" in text or "Interne Nr." in text:
            pick = "tender_detail"
        elif "Treffer" in text or "Ausschreibungen" in text:
            pick = "tender_list"
        else:
            pick = "other"
        probs = {k: (0.9 if k == pick else 0.1 / max(1, len(criteria) - 1)) for k in criteria}
        return Choice(pick, probs, 0.9, "fake")

    def _pick_candidate(self, state, criteria):
        field = state["field"]
        best = "none"
        for k, v in state["candidates"].items():
            ctx = v["context"].lower()
            if field == "submissionDeadline" and ("frist" in ctx or "deadline" in ctx):
                best = k
            if field == "publicationDate" and "veröffentlicht" in ctx:
                best = k
            if field == "referenceNumber" and "vergabenummer" in ctx:
                best = k
        probs = {k: (0.9 if k == best else 0.1 / max(1, len(criteria) - 1)) for k in criteria}
        return Choice(best, probs, 0.9, "fake")

    async def nouls(self, state, questions, *, portal_id=None, tag=""):
        self.calls += 1
        out = {}
        goal = state.get("goal", "")
        for k in questions:
            if k.startswith("link_"):
                link = state["links"][int(k.split("_")[1])]
                out[k] = self._link(goal, link)
            elif k.startswith("field_"):
                out[k] = self._field(state, k[len("field_"):])
            else:
                out[k] = 0.5
        return out

    @staticmethod
    def _link(goal, link):
        t, h = link["text"].lower(), link["href"].lower()
        if goal.startswith("platform"):
            return 0.95 if "/lab/" in h and "europa.eu" not in h and h.endswith("/") else 0.05
        if goal.startswith("detail"):
            return 0.9 if "/tender/" in h and "unterlagen" not in h else 0.05
        if goal.startswith("documents"):
            return 0.9 if (h.endswith(".pdf") or h.endswith(".zip") or "unterlagen" in t) else 0.05
        if goal.startswith("open_list"):
            return 0.9 if re.search(r"/lab/[a-z-]+/$", h) else 0.05
        return 0.05

    @staticmethod
    def _field(state, field):
        value = str(state.get("tender", {}).get(field, ""))
        page = state.get("page", "")
        if field == "cpvCodes":
            return 0.9 if all(c in page for c in value.split(", ")) else 0.1
        probe = value.split(" (")[0]
        if probe and probe in page:
            return 0.9
        if " (" in value and value.endswith(")"):
            iso = value.split(" (", 1)[1][:-1]
            iso_naive = re.sub(r"(?:Z|[+-]\d{2}:\d{2})$", "", iso)
            if iso in page or iso_naive in page:
                return 0.9
        return 0.1


class NoJudge:
    client = None

    async def choice(self, *a, **k):
        from watchtower.judge.jev import JudgeUnavailable

        raise JudgeUnavailable("none")

    async def nouls(self, *a, **k):
        from watchtower.judge.jev import JudgeUnavailable

        raise JudgeUnavailable("none")
