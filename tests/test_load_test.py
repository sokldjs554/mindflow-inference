from scripts.load_test import latency_summary


def test_latency_summary_uses_nearest_rank_and_empty_is_explicit() -> None:
    summary = latency_summary([4.0, 1.0, 3.0, 2.0])
    assert summary == {
        "count": 4,
        "p50_ms": 2.0,
        "p95_ms": 4.0,
        "p99_ms": 4.0,
        "max_ms": 4.0,
    }
    empty = latency_summary([])
    assert empty["count"] == 0
    assert empty["p95_ms"] is None
