from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from ipaddress import IPv4Interface
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter

from scripts.ci_business_witness.oracle import require

EXECUTION_SECONDS = 1800.0
CLEANUP_SECONDS = 120.0
LOCAL_DOCKER_HOST = "unix:///var/run/docker.sock"


class _NetworkEndpoint(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    IPv4Address: IPv4Interface


class _InternalNetwork(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    Name: str
    Driver: Literal["bridge"]
    Internal: bool
    Labels: dict[str, str]
    Containers: dict[str, _NetworkEndpoint]


def database_host(network_json: str, *, project: str, container: str) -> str:
    networks = TypeAdapter(list[_InternalNetwork]).validate_json(network_json)
    require(len(networks) == 1, "database-network-population")
    network = networks[0]
    require(
        network.Internal
        and network.Name == f"{project}_isolated"
        and network.Labels.get("com.docker.compose.project") == project
        and container in network.Containers,
        "database-network-identity",
    )
    address = network.Containers[container].IPv4Address.ip
    require(
        address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified,
        "database-network-address",
    )
    return str(address)


class WitnessBudget:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.execution_deadline = clock() + EXECUTION_SECONDS
        self.cleanup_deadline: float | None = None

    def remaining(self) -> float:
        deadline = self.cleanup_deadline
        return (self.execution_deadline if deadline is None else deadline) - self.clock()

    def timeout(self, step_limit: float) -> float:
        remaining = self.remaining()
        require(remaining > 0, "whole-witness-deadline")
        require(step_limit > 0, "invalid-step-budget")
        return min(step_limit, remaining)

    def begin_cleanup(self) -> float:
        if self.cleanup_deadline is None:
            self.cleanup_deadline = self.clock() + CLEANUP_SECONDS
        return self.timeout(CLEANUP_SECONDS)


def admit_docker_environment(environment: Mapping[str, str]) -> None:
    require(
        not any(
            environment.get(key)
            for key in (
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
            )
        ),
        "ambient-docker-selector",
    )


def local_docker_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    return ("--host", LOCAL_DOCKER_HOST, *arguments)
