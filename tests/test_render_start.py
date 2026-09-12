import os

import pytest

from scripts.render_start import configure_environment, wait_for_schema


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+asyncpg"])
def test_render_environment_preserves_credentials_and_sets_origin(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    suffix = "demo:p%40ss@internal-db:5432/demo"
    monkeypatch.setenv("DATABASE_URL", f"{scheme}://{suffix}")
    monkeypatch.setenv("REDIS_URL", "redis://internal-redis:6379")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.invalid")
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:8000"]')
    configure_environment("web")
    assert os.environ["DATABASE_URL"] == f"postgresql+asyncpg://{suffix}"
    assert os.environ["CORS_ORIGINS"] == '["https://example.invalid"]'


@pytest.mark.parametrize("key", ["DATABASE_URL", "REDIS_URL"])
def test_render_refuses_local_datastores(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo@internal-db/demo")
    monkeypatch.setenv("REDIS_URL", "redis://internal-redis:6379")
    monkeypatch.setenv(
        key, "postgresql://localhost/demo" if key == "DATABASE_URL" else "redis://127.0.0.1:6379"
    )
    with pytest.raises(ValueError, match="Render datastore"):
        configure_environment("worker")


async def test_worker_schema_gate_accepts_migrated_database(migrated: None) -> None:
    await wait_for_schema(timeout_seconds=5)


async def test_worker_schema_gate_times_out_without_starting_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://demo@127.0.0.1:1/demo")
    with pytest.raises(SystemExit, match="worker was not started"):
        await wait_for_schema(timeout_seconds=0.05)
