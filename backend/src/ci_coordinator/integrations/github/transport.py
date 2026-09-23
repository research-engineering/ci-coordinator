from __future__ import annotations

from typing import Protocol

from ci_coordinator.integrations.github.contracts import GitHubRequest, GitHubTransportResult


class GitHubTransport(Protocol):
    """Provider-I/O port; adapters exchange only immutable protocol facts with it."""

    async def send(self, request: GitHubRequest) -> GitHubTransportResult: ...


class InstallationTransportFactory(Protocol):
    """Create an installation-bound transport without exposing credentials."""

    def for_installation(self, installation_id: int) -> GitHubTransport: ...
