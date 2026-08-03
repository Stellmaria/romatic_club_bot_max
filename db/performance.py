"""Low-overhead database latency and pool-saturation measurements.

The module intentionally has no metrics-backend dependency. Runtime code records
bounded in-memory samples and structured log events; an exporter can scrape the
snapshot later without changing query call sites.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, AsyncIterator

logger = logging.getLogger("auction_bot.database.performance")

_DEFAULT_SLOW_QUERY_SECONDS = 0.250
_MAX_SAMPLES = 2_048


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _pool_state(pool: Any | None) -> tuple[int, int, float]:
    if pool is None:
        return 0, 0, 0.0
    get_size = getattr(pool, "get_size", None)
    get_idle_size = getattr(pool, "get_idle_size", None)
    if not callable(get_size) or not callable(get_idle_size):
        return 0, 0, 0.0
    try:
        size = max(0, int(get_size()))
        idle = max(0, int(get_idle_size()))
    except (TypeError, ValueError):
        return 0, 0, 0.0
    used = max(0, size - idle)
    utilization = (used / size) if size else 0.0
    return size, idle, utilization


@dataclass(slots=True)
class _OperationMetrics:
    durations_ms: deque[float] = field(default_factory=lambda: deque(maxlen=_MAX_SAMPLES))
    calls: int = 0
    failures: int = 0
    slow_calls: int = 0
    max_pool_utilization: float = 0.0

    def record(
        self,
        *,
        duration_ms: float,
        failed: bool,
        slow: bool,
        pool_utilization: float,
    ) -> None:
        self.calls += 1
        self.failures += int(failed)
        self.slow_calls += int(slow)
        self.durations_ms.append(duration_ms)
        self.max_pool_utilization = max(self.max_pool_utilization, pool_utilization)

    def snapshot(self) -> dict[str, int | float]:
        samples = list(self.durations_ms)
        return {
            "round_trips": self.calls,
            "failures": self.failures,
            "slow_queries": self.slow_calls,
            "p50_ms": round(_percentile(samples, 0.50), 3),
            "p95_ms": round(_percentile(samples, 0.95), 3),
            "max_ms": round(max(samples, default=0.0), 3),
            "max_pool_utilization": round(self.max_pool_utilization, 4),
        }


class DatabasePerformanceRegistry:
    """Bounded process-local metrics for critical PostgreSQL operations."""

    def __init__(self) -> None:
        self._operations: defaultdict[str, _OperationMetrics] = defaultdict(_OperationMetrics)

    def record(
        self,
        operation: str,
        *,
        duration_ms: float,
        failed: bool,
        slow: bool,
        pool_utilization: float,
    ) -> None:
        self._operations[operation].record(
            duration_ms=duration_ms,
            failed=failed,
            slow=slow,
            pool_utilization=pool_utilization,
        )

    def snapshot(self) -> dict[str, dict[str, int | float]]:
        return {
            operation: metrics.snapshot()
            for operation, metrics in sorted(self._operations.items())
        }

    def reset(self) -> None:
        self._operations.clear()


_registry = DatabasePerformanceRegistry()


@asynccontextmanager
async def track_database_query(
    operation: str,
    *,
    pool: Any | None = None,
    slow_query_seconds: float = _DEFAULT_SLOW_QUERY_SECONDS,
) -> AsyncIterator[None]:
    """Measure one database round trip without logging SQL or bind values."""

    started = perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        duration_seconds = perf_counter() - started
        duration_ms = duration_seconds * 1_000.0
        size, idle, utilization = _pool_state(pool)
        slow = duration_seconds >= max(0.0, float(slow_query_seconds))
        _registry.record(
            operation,
            duration_ms=duration_ms,
            failed=failed,
            slow=slow,
            pool_utilization=utilization,
        )
        if slow:
            logger.warning(
                "Slow database operation operation=%s duration_ms=%.1f pool_size=%d "
                "pool_idle=%d pool_utilization=%.3f failed=%s",
                operation,
                duration_ms,
                size,
                idle,
                utilization,
                failed,
            )
        elif size and idle == 0:
            logger.warning(
                "Database pool saturated operation=%s pool_size=%d duration_ms=%.1f",
                operation,
                size,
                duration_ms,
            )


def database_performance_snapshot() -> dict[str, dict[str, int | float]]:
    return _registry.snapshot()


def reset_database_performance_metrics() -> None:
    _registry.reset()


__all__ = [
    "DatabasePerformanceRegistry",
    "database_performance_snapshot",
    "reset_database_performance_metrics",
    "track_database_query",
]
