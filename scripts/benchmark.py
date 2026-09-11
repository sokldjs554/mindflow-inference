"""Measure real EXPLAIN ANALYZE on isolated synthetic temporary tables; no production DDL."""

import argparse
import asyncio
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db import engine


async def measure(rows: int, output: Path) -> None:
    async with engine.connect() as db:
        await db.execute(
            text(
                "CREATE TEMP TABLE perf_runs AS SELECT "
                "md5(i::text)::uuid AS id, md5((i % 1000)::text)::uuid AS session_id, "
                "now() - i * interval '1 second' AS created_at, "
                "CASE WHEN i % 100 = 0 THEN 'REVIEW_REQUIRED' ELSE 'APPROVED' END AS status, "
                "jsonb_build_object('synthetic', repeat('x', 512)) AS payload "
                "FROM generate_series(1, :rows) AS g(i)"
            ),
            {"rows": rows},
        )
        await db.execute(text("ANALYZE perf_runs"))
        queries = {
            "session_runs": "SELECT id, created_at FROM perf_runs "
            "WHERE session_id = md5('42')::uuid ORDER BY created_at DESC, id DESC LIMIT 20",
            "review_queue": "SELECT id, created_at FROM perf_runs WHERE status = 'REVIEW_REQUIRED' "
            "ORDER BY created_at DESC, id DESC LIMIT 20",
        }
        results: dict[str, Any] = {
            "measured_at": datetime.now(UTC).isoformat(),
            "rows": rows,
            "postgres": await db.scalar(text("SELECT version()")),
            "method": "Five warm EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) samples per query; "
            "temporary synthetic projection, no production table/index changes",
            "queries": {},
        }

        async def sample(query: str) -> dict[str, Any]:
            plans = []
            for _ in range(5):
                raw = await db.scalar(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query))
                plan = json.loads(raw) if isinstance(raw, str) else raw
                plans.append(plan[0])
            return {
                "median_execution_ms": statistics.median(p["Execution Time"] for p in plans),
                "samples_ms": [p["Execution Time"] for p in plans],
                "plan": plans[-1],
            }

        for name, query in queries.items():
            results["queries"][name] = {"sql": query, "before": await sample(query)}
        await db.execute(
            text("CREATE INDEX perf_session_cursor ON perf_runs(session_id,created_at,id)")
        )
        await db.execute(
            text(
                "CREATE INDEX perf_review_cursor ON perf_runs(created_at,id) "
                "WHERE status='REVIEW_REQUIRED'"
            )
        )
        await db.execute(text("ANALYZE perf_runs"))
        for name, query in queries.items():
            results["queries"][name]["after"] = await sample(query)
        # Temp table is discarded on connection close; transaction is intentionally rolled back.
        output.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output.write_text, json.dumps(results, indent=2), encoding="utf-8")
        for name, data in results["queries"].items():
            print(
                f"{name}: {data['before']['median_execution_ms']} ms -> "
                f"{data['after']['median_execution_ms']} ms (median, synthetic only)"
            )
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--output", type=Path, default=Path("reports/local/performance.json"))
    args = parser.parse_args()
    if not 1000 <= args.rows <= 2_000_000:
        parser.error("rows must be between 1000 and 2000000")
    asyncio.run(measure(args.rows, args.output))


if __name__ == "__main__":
    main()
