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


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
