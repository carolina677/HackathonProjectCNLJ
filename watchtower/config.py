from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    typesafe_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    lab: bool = False
    data_dir: Path = Path("/data")
    read_interval_seconds: int = 600
    min_request_interval: float = 1.0

    def __init__(self, **kw):
        import os

        if "data_dir" not in kw and os.environ.get("WATCHTOWER_DATA"):
            kw["data_dir"] = Path(os.environ["WATCHTOWER_DATA"])
        if "lab" not in kw and os.environ.get("WATCHTOWER_LAB"):
            kw["lab"] = os.environ["WATCHTOWER_LAB"] in ("1", "true", "yes")
        super().__init__(**kw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
