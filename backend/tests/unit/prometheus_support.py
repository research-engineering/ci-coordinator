from __future__ import annotations

from prometheus_client.parser import text_string_to_metric_families

from ci_coordinator.observability import RuntimeMetrics

type SampleKey = tuple[str, tuple[tuple[str, str], ...]]


def prometheus_samples(metrics: RuntimeMetrics) -> dict[SampleKey, float]:
    content = metrics.snapshot().content.decode("utf-8")
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): float(sample.value)
        for family in text_string_to_metric_families(content)
        for sample in family.samples
    }
