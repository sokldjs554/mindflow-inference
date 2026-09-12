"""Render-only environment adapter; local Docker commands remain unchanged."""

import argparse
import asyncio
import json
import os
import sys
from urllib.parse import urlsplit

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine


def configure_environment(mode: str) -> None:
    database_url = os.environ["DATABASE_URL"]
    for scheme in ("postgres://", "postgresql://"):
        if database_url.startswith(scheme):
            database_url = "postgresql+asyncpg://" + database_url[len(scheme) :]
            break
    if not database_url.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL must use PostgreSQL with asyncpg")
    os.environ["DATABASE_URL"] = database_url
    for key in ("DATABASE_URL", "REDIS_URL"):
        host = urlsplit(os.environ[key]).hostname
        if not host or host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
            raise ValueError(f"{key} must reference a Render datastore")
    if mode == "web":
        origin = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path:
            raise ValueError("RENDER_EXTERNAL_URL must be an HTTPS origin")
        os.environ["CORS_ORIGINS"] = json.dumps([origin])
        port = int(os.environ["PORT"])
        if not 1 <= port <= 65535:
            raise ValueError("PORT is out of range")


async def wait_for_schema(timeout_seconds: float = 300) -> None:
    """Only web migrates; worker waits for this release's complete Alembic heads."""
    heads = set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    try:
        async with asyncio.timeout(timeout_seconds):
            while True:
                try:
                    async with engine.connect() as connection:
                        revisions = set(
                            (
                                await connection.execute(
                                    text("SELECT version_num FROM alembic_version")
                                )
                            )
                            .scalars()
                            .all()
                        )
                        if revisions == heads:
                            return
                except (SQLAlchemyError, OSError):
                    pass
                await asyncio.sleep(2)
    except TimeoutError:
        raise SystemExit("Migration readiness timed out; worker was not started") from None
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("migrate", "web", "worker"))
    mode = parser.parse_args().mode
    configure_environment(mode)
    if mode == "migrate":
        command = [sys.executable, "-m", "alembic", "upgrade", "head"]
    elif mode == "worker":
        asyncio.run(wait_for_schema())
        command = [sys.executable, "-m", "app.worker"]
    else:
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            os.environ["PORT"],
            "--no-access-log",
        ]
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
