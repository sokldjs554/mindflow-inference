from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://mindflow:local-demo-only@127.0.0.1:5432/mindflow"
    redis_url: str = "redis://127.0.0.1:6379/0"
    api_key: str = ""
    cors_origins: list[str] = ["http://localhost:8000"]
    provider_timeout_seconds: float = Field(default=5, gt=0, le=60)
    max_attempts: int = Field(default=3, ge=1, le=5)
    retry_base_seconds: float = Field(default=1, gt=0)
    rate_limit: int = Field(default=120, ge=1)
    max_body_bytes: int = 5_500_000
    stream_prefix: str = "mindflow"
    worker_metrics_port: int = Field(default=9000, ge=1024, le=65535)
    worker_metrics_host: str = "127.0.0.1"


@lru_cache
def settings() -> Settings:
    return Settings()
