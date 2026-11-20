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
    read_interval_seconds: int = 12 * 3600  # reads cost no LLM; one Jev spot check each
    pulse_interval_seconds: int = 15 * 60  # list-only check between full reads, no model call; 0 = off
    scheduler_tick_seconds: float = 5.0
    min_request_interval: float = 1.0
    llm_price_in: float | None = None
    llm_price_out: float | None = None
    mlflow_tracking_uri: str | None = None
    mlflow_experiment_name: str = "watchtower"

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
