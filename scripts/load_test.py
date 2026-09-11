"""Bounded synthetic REST -> DB/outbox -> Redis/worker -> WS -> human review load."""

import argparse
import asyncio
import json
import math
import os
import platform
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect


class LoadFailure(Exception):
    """A sanitized workload failure code."""


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    """Nearest-rank percentiles in milliseconds; empty samples have no percentiles."""
    ordered = sorted(values)

    def percentile(q: float) -> float | None:
        return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else None

    return {
        "count": len(values),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_ms": ordered[-1] if ordered else None,
    }


class Recorder:
    def __init__(self) -> None:
        self.http_ms: list[float] = []
        self.http_status: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.flow_ms: list[float] = []
        self.inference_ms: list[float] = []
        self.ws_states: Counter[str] = Counter()
        self.ws_active = 0
        self.ws_peak = 0
        self.ws_opened = 0
        self.jobs: list[str] = []

    async def request(
        self, client: httpx.AsyncClient, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        started = time.perf_counter()
        try:
            response = await client.request(
                method, path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())}
            )
            self.http_status[str(response.status_code)] += 1
            if response.is_error:
                raise LoadFailure(f"HTTP_{response.status_code}")
            return response.json()
        except httpx.HTTPError as exc:
            self.http_status["transport_error"] += 1
            raise LoadFailure(type(exc).__name__) from None
        finally:
            self.http_ms.append((time.perf_counter() - started) * 1000)


async def flow(
    client: httpx.AsyncClient, stats: Recorder, url: str, key: str, number: int, job_timeout: float
) -> None:
    started = time.perf_counter()
    async with asyncio.timeout(job_timeout):
        session = await stats.request(
            client, "POST", "/api/sessions", {"title": f"Synthetic load {number}"}
        )
        sid = session["id"]
        await stats.request(
            client,
            "POST",
            f"/api/sessions/{sid}/transcript",
            {
                "utterances": [
                    {
                        "sequence": 1,
                        "speaker": "client",
                        "text": "Synthetic interview: I recorded my sleep duration.",
                    }
                ]
            },
        )
        inference_started = time.perf_counter()
        job = await stats.request(client, "POST", f"/api/sessions/{sid}/inferences", {})
        stats.jobs.append(job["id"])
        parts = urlsplit(url)
        ws_url = urlunsplit(
            (
                "wss" if parts.scheme == "https" else "ws",
                parts.netloc,
                f"/ws/jobs/{job['id']}",
                "",
                "",
            )
        )
        async with connect(ws_url, open_timeout=job_timeout, close_timeout=2) as ws:
            stats.ws_opened += 1
            stats.ws_active += 1
            stats.ws_peak = max(stats.ws_peak, stats.ws_active)
            try:
                if key:
                    await ws.send(json.dumps({"api_key": key}))
                while True:
                    event = json.loads(await ws.recv())
                    if event.get("type") == "heartbeat":
                        continue
                    state = event["state"]
                    stats.ws_states[state] += 1
                    if state == "FAILED":
                        raise LoadFailure("JOB_FAILED")
                    if state in {"REVIEW_REQUIRED", "COMPLETED"}:
                        break
            finally:
                stats.ws_active -= 1
        result = await stats.request(client, "GET", f"/api/jobs/{job['id']}/result")
        if (
            result["status"] != "REVIEW_REQUIRED"
            or not result["statements"]
            or any(s["current_validation"] != "SUPPORTED" for s in result["statements"])
        ):
            raise LoadFailure("INVALID_RESULT")
        stats.inference_ms.append((time.perf_counter() - inference_started) * 1000)
        review = await stats.request(
            client,
            "POST",
            f"/api/runs/{result['run_id']}/reviews",
            {
                "action": "approve",
                "reviewer": "synthetic-load",
                "reason": "Synthetic exact evidence checked by load harness",
            },
        )
        completed = await stats.request(client, "GET", f"/api/jobs/{job['id']}")
        if review["status"] != "APPROVED" or completed["state"] != "COMPLETED":
            raise LoadFailure("REVIEW_NOT_COMPLETED")
        stats.flow_ms.append((time.perf_counter() - started) * 1000)


async def run_load(
    url: str, jobs: int, concurrency: int, job_timeout: float, environment: str, key: str = ""
) -> dict[str, Any]:
    if not 1 <= jobs <= 100 or not 1 <= concurrency <= 10 or not 1 <= job_timeout <= 60:
        raise ValueError("jobs 1..100, concurrency 1..10, timeout 1..60 required")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.query:
        raise ValueError("Use an HTTP(S) origin without embedded credentials or query parameters")
    stats = Recorder()
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        base_url=url, timeout=job_timeout, headers={"X-API-Key": key}
    ) as client:
        # Preflight is excluded from load counts; no sessions created if not ready.
        response = await client.get("/ready")
        response.raise_for_status()
        started_at = datetime.now(UTC).isoformat()
        start = time.perf_counter()

        async def bounded(number: int) -> None:
            async with semaphore:
                try:
                    await flow(client, stats, url, key, number, job_timeout)
                except Exception as exc:
                    code = str(exc) if isinstance(exc, LoadFailure) else type(exc).__name__
                    stats.errors[code] += 1  # Never store response/exception text or inputs.

        tasks = [asyncio.create_task(bounded(n)) for n in range(jobs)]
        global_timeout = False
        try:
            async with asyncio.timeout(min(600, math.ceil(jobs / concurrency) * job_timeout + 5)):
                await asyncio.gather(*tasks)
        except TimeoutError:
            global_timeout = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.perf_counter() - start
    successes = len(stats.flow_ms)
    if global_timeout:
        stats.errors["GLOBAL_TIMEOUT"] += jobs - successes - sum(stats.errors.values())
    return {
        "measured_at": started_at,
        "environment": {
            "server_description": environment,
            "client_platform": platform.platform(),
            "python": platform.python_version(),
        },
        "configuration": {
            "jobs": jobs,
            "concurrency": concurrency,
            "job_timeout_seconds": job_timeout,
            "websocket": True,
            "synthetic_only": True,
        },
        "duration_seconds": elapsed,
        "success_count": successes,
        "error_count": jobs - successes,
        "errors": dict(stats.errors),
        "created_job_count": len(stats.jobs),
        "http_request_count": len(stats.http_ms),
        "http_status_counts": dict(stats.http_status),
        "http_latency": latency_summary(stats.http_ms),
        "inference_completion_latency": latency_summary(stats.inference_ms),
        "workflow_latency": latency_summary(stats.flow_ms),
        "successful_workflows_per_second": successes / elapsed,
        "http_requests_per_second": len(stats.http_ms) / elapsed,
        "websocket": {
            "opened": stats.ws_opened,
            "peak_connections": stats.ws_peak,
            "observed_states": dict(stats.ws_states),
        },
        "method": "Finite closed-loop concurrency; six REST requests per successful workflow. "
        "Latency uses client wall clock; nearest-rank percentiles. Preflight excluded. "
        "Workflow ends after validated result, synthetic approval and COMPLETED job. "
        "No warm-up, retries by the client, or cleanup of persisted synthetic sessions.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30, dest="job_timeout")
    parser.add_argument(
        "--environment", required=True, help="Non-sensitive server test environment"
    )
    parser.add_argument("--output", type=Path, default=Path("reports/local/load.json"))
    args = parser.parse_args()
    try:
        report = asyncio.run(
            run_load(
                args.url,
                args.jobs,
                args.concurrency,
                args.job_timeout,
                args.environment,
                os.getenv("API_KEY", ""),
            )
        )
    except (ValueError, httpx.HTTPError) as exc:
        parser.exit(2, f"Preflight/configuration failed: {type(exc).__name__}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["error_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
