import httpx
import respx

from watchtower.briefing import briefing_text, synthesize
from watchtower.db import PortalRow
from watchtower.models import PortalState


def _p(i, state, reason=None, url=None):
    return PortalRow(id=f"p{i}", url=url or f"https://portal{i}.example/ausschreibungen", state=state, reason=reason,
                     tenderCount=10, lastSuccessAt=None, recipe=None, snapshot_ref=None,
                     country=None, prev_count=10)


def test_text_counts_repairs_and_failures():
    portals = [_p(i, PortalState.serving) for i in range(14)]
    portals.append(_p(99, PortalState.failed, "Portal requires a login", "https://vergabe.example.de/start"))
    runs = [{"portal_id": "p1", "kind": "repair", "result": "ok"}, {"portal_id": "p2", "kind": "repair", "result": "ok"},
            {"portal_id": "p3", "kind": "read", "result": "ok"}]
    text = briefing_text(portals, runs)
    assert "14" in text and "2" in text and "login" in text and "vergabe.example.de" in text
    assert len(text.split()) <= 70


def test_text_when_all_is_well():
    text = briefing_text([_p(i, PortalState.serving) for i in range(3)], [])
    assert "3" in text and "need" not in text.lower()


@respx.mock
async def test_synthesize_calls_elevenlabs(settings):
    s = settings.model_copy(update={"elevenlabs_api_key": "k", "elevenlabs_voice_id": "VOICE1"})
    route = respx.post("https://api.elevenlabs.io/v1/text-to-speech/VOICE1").mock(
        return_value=httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"}))
    assert await synthesize("Good morning.", s) == b"ID3mp3"
    req = route.calls[0].request
    assert req.headers["xi-api-key"] == "k" and b"eleven_multilingual_v2" in req.content


async def test_routes_without_key(client, repo):
    await repo.create_portal("https://a.example/")
    assert (await client.get("/briefing.mp3")).status_code == 404
    r = await client.get("/briefing.txt")
    assert r.status_code == 200 and "portal" in r.text.lower()
    assert "briefing" in (await client.get("/")).text.lower()
