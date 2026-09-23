"""Prometheus registry ownership isolated from domain decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from prometheus_client import CollectorRegistry, GCCollector, PlatformCollector, ProcessCollector
from prometheus_client.exposition import CONTENT_TYPE_LATEST, generate_latest

PROMETHEUS_CONTENT_TYPE: Final = CONTENT_TYPE_LATEST


@dataclass(frozen=True, slots=True)
class PrometheusSnapshot:
    content: bytes
    content_type: str = PROMETHEUS_CONTENT_TYPE


class MetricsRegistry:
    """Own one process-local registry exported through the Prometheus protocol."""

    def __init__(self) -> None:
        self._registry = CollectorRegistry(auto_describe=True)
        GCCollector(registry=self._registry)
        PlatformCollector(registry=self._registry)
        ProcessCollector(registry=self._registry)

    @property
    def collector_registry(self) -> CollectorRegistry:
        return self._registry

    def snapshot(self) -> PrometheusSnapshot:
        return PrometheusSnapshot(generate_latest(self._registry))
