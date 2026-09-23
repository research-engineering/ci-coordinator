from typing import Never, cast

import pytest
from prometheus_support import prometheus_samples

from ci_coordinator.observability import RuntimeMetrics


def test_worker_presence_is_lazy_lane_bounded_and_independent_from_reconciliation() -> None:
    metrics = RuntimeMetrics()
    name = "ci_coordinator_ci_history_worker_running"
    assert not any(key[0] == name for key in prometheus_samples(metrics))
    before = prometheus_samples(metrics)
    metrics.ci_history_worker_running("backfill", True)
    metrics.ci_history_worker_running("backfill", False)
    metrics.ci_history_worker_running("private-repository", True)
    samples = prometheus_samples(metrics)
    assert samples[name, (("lane", "backfill"),)] == 0
    assert samples[name, (("lane", "other"),)] == 1
    assert not any("private-repository" in str(key) for key in samples)
    assert {key: value for key, value in samples.items() if "reconciliation" in key[0]} == {
        key: value for key, value in before.items() if "reconciliation" in key[0]
    }


def test_invalid_worker_boolean_and_exporter_failure_do_not_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = RuntimeMetrics()
    metrics.ci_history_worker_running("backfill", cast(bool, 1))

    def fail(*labels: object) -> Never:
        raise RuntimeError("private exporter detail")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(metrics._ci_history_workers_running, "labels", fail)
    metrics.ci_history_worker_running("backfill", True)
    assert (
        prometheus_samples(metrics)[
            "ci_coordinator_instrumentation_failures_total", (("surface", "background"),)
        ]
        == 2
    )
