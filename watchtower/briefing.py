"""Spoken morning briefing: a deterministic summary of the night, voiced by ElevenLabs (optional)."""

from urllib.parse import urlsplit

import httpx

from .models import PortalState

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"


def _name(url: str) -> str:
    u = urlsplit(url)
    return (u.netloc + u.path.rstrip("/"))[:50] or url


def _n(k: int, one: str, many: str) -> str:
    return f"{k} {one if k == 1 else many}"


def briefing_text(portals, runs_last_24h: list[dict]) -> str:
    """Facts only, from portal states and the last day's runs; at most ~70 words."""
    if not portals:
        return "Good morning. No portals are being watched yet."
    by = {s: [p for p in portals if p.state == s] for s in PortalState}
    serving, healing, failed = by[PortalState.serving], by[PortalState.healing], by[PortalState.failed]
    tenders = sum(p.tenderCount for p in portals)
    repaired = {r["portal_id"] for r in runs_last_24h if r.get("kind") == "repair" and r.get("result") == "ok"}
    parts = [f"Good morning. {len(serving)} of {_n(len(portals), 'portal', 'portals')} are delivering, "
             f"{_n(tenders, 'tender', 'tenders')} in total."]
    if repaired:
        parts.append(f"{len(repaired)} broke in the last day and repaired themselves.")
    if healing:
        parts.append(f"{len(healing)} {'is' if len(healing) == 1 else 'are'} being repaired right now.")
    if by[PortalState.integrating]:
        parts.append(f"{len(by[PortalState.integrating])} still being learned.")
    if failed:
        parts.append(f"{len(failed)} {'needs' if len(failed) == 1 else 'need'} you:")
        for p in failed[:2]:
            parts.append(f"{_name(p.url)}, {(p.reason or 'it cannot deliver').rstrip('.')}.")
        if len(failed) > 2:
            parts.append(f"And {len(failed) - 2} more on the dashboard.")
    else:
        parts.append("Everything is healthy.")
    text = " ".join(parts)
    words = text.split()
    return text if len(words) <= 70 else " ".join(words[:68]) + " …"


async def synthesize(text: str, settings) -> bytes:
    if not (settings.elevenlabs_api_key and settings.elevenlabs_voice_id):
        raise RuntimeError("ElevenLabs is not configured")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(ELEVENLABS_URL.format(voice=settings.elevenlabs_voice_id),
                         headers={"xi-api-key": settings.elevenlabs_api_key, "accept": "audio/mpeg"},
                         json={"text": text, "model_id": "eleven_multilingual_v2"})
        r.raise_for_status()
        return r.content
