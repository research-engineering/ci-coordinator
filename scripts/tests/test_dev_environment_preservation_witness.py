from __future__ import annotations

from pathlib import Path

import pytest
from scripts.dev_environment import preservation_witness as owner
from scripts.dev_environment.compose import ComposeError, ProviderInvocation, ServiceRuntimeIdentity
from scripts.dev_environment.identity import derive_instance_identity
from scripts.dev_environment.private_files import (
    atomic_write_private_text,
    ensure_private_directory,
)


class PreservedProject:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runtime = {
            service: ServiceRuntimeIdentity(letter * 64, "initial-start")
            for service, letter in (("postgres", "a"), ("backend", "b"), ("frontend", "c"))
        }

    def watch_invocation(self) -> ProviderInvocation:
        return ProviderInvocation(("docker", "compose", "watch", "--no-up"), self.root, {})

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
        return self.runtime[service]


@pytest.mark.parametrize(
    "changed", ["none", "credentials", "metadata", "data", "postgres", "backend", "frontend"]
)
@pytest.mark.parametrize("all_services", [False, True])
def test_preservation_rejects_each_independent_protected_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str, all_services: bool
) -> None:
    identity = derive_instance_identity(
        tmp_path, state_home=tmp_path.parent / f"{tmp_path.name}-state"
    )
    ensure_private_directory(identity.state_directory)
    ensure_private_directory(identity.secrets_directory)
    credential = identity.secrets_directory / "credential"
    for path in (credential, identity.metadata_path, identity.environment_path):
        atomic_write_private_text(path, "original")
    project = PreservedProject(tmp_path)
    token = "d" * 32
    monkeypatch.setattr(
        "scripts.dev_environment.preservation_witness.secrets.token_hex", lambda _: token
    )
    monkeypatch.setattr(owner, "_query", lambda *_args: token)
    preservation = owner.StackPreservation(identity, project, all_services=all_services)
    preservation.verify()
    if changed == "credentials":
        atomic_write_private_text(credential, "changed")
    elif changed == "metadata":
        atomic_write_private_text(identity.metadata_path, "changed")
    elif changed == "data":
        monkeypatch.setattr(owner, "_query", lambda *_args: "different-data")
    elif changed in project.runtime:
        project.runtime[changed] = ServiceRuntimeIdentity("e" * 64, "restarted")
    protected = changed != "none" and (all_services or changed not in {"backend", "frontend"})
    if protected:
        with pytest.raises(ComposeError):
            preservation.verify()
    else:
        preservation.verify()
