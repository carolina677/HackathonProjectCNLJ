import httpx
import pytest

from watchtower.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, lab=True, typesafe_api_key=None,
                    anthropic_api_key=None, openai_api_key=None, read_interval_seconds=3600)


@pytest.fixture
async def app(settings):
    from watchtower.app import create_app

    app = create_app(settings, start_scheduler=False)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def repo(app):
    return app.state.repo


class Lab:
    def __init__(self, base: str):
        self.base = base

    def url(self, slug: str) -> str:
        return f"{self.base}/lab/{slug}/"

    def doc_url(self, slug: str, tender_id: str, n: int) -> str:
        return f"{self.base}/lab/{slug}/doc/{tender_id}/{n}.pdf"

    def truth(self, slug: str):
        from watchtower.lab.generator import truth

        return truth(slug, self.base)

    def mutate(self, slug: str, name: str):
        from watchtower.lab.app import STATE

        STATE.mutate(slug, name)

    def reset(self, slug: str | None = None):
        from watchtower.lab.app import STATE

        STATE.reset(slug)


@pytest.fixture(scope="session")
def lab_server():
    import socket
    import threading
    import time

    import uvicorn
    from fastapi import FastAPI

    from watchtower.lab.app import create_lab_app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    host_app = FastAPI()
    host_app.mount("/lab", create_lab_app())
    server = uvicorn.Server(uvicorn.Config(host_app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    yield Lab(f"http://127.0.0.1:{port}")
    server.should_exit = True
    th.join(timeout=5)


@pytest.fixture
def lab(lab_server):
    lab_server.reset()
    yield lab_server
    lab_server.reset()


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
