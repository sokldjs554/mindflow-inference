import json
import logging
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

HTTP_LATENCY = Histogram("mindflow_http_seconds", "HTTP latency", ["method", "route", "status"])
INFERENCE_LATENCY = Histogram("mindflow_inference_seconds", "Successful inference latency")
JOB_FAILURES = Counter("mindflow_job_failures_total", "Terminal job failures", ["code"])
PROVIDER_FAILURES = Counter("mindflow_provider_failures_total", "Provider failures", ["code"])
VALIDATION_FAILURES = Counter("mindflow_validation_failures_total", "Unsafe statements", ["status"])
WS_CONNECTIONS = Gauge("mindflow_websocket_connections", "Active websocket connections")
logger = logging.getLogger("mindflow")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def log(event: str, **context: Any) -> None:
    allowed = {"request_id", "correlation_id", "job_id", "run_id", "state", "code", "attempt"}
    logger.info(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                **{k: str(v) for k, v in context.items() if k in allowed},
            }
        )
    )
