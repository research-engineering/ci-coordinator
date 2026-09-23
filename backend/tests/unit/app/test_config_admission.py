from __future__ import annotations

import asyncio
from typing import cast

import pytest
from config_epoch_support import CONFIG_SOURCE, admitted_config_epoch
from repository_activation_support import ACTOR, SCOPE

from ci_coordinator.app.config_admission import (
    ConfigAdmissionAccepted,
    ConfigAdmissionForbidden,
    ConfigAdmissionInvalid,
    ConfigAdmissionService,
    ConfigAdmissionUnavailable,
    PolicyAdmissionUnavailable,
)
from ci_coordinator.config_control import (
    PolicyAdmissionResult,
    PolicyDiagnostic,
    PolicySourceFormat,
    RepositoryScope,
    admit_policy_document,
)


class _Authorizer:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, RepositoryScope]] = []

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.calls.append((actor, scope))
        return self.allowed


async def _admit(source: bytes, source_format: PolicySourceFormat) -> PolicyAdmissionResult:
    return admit_policy_document(source, source_format)


def test_admission_returns_the_exact_validated_draft_after_scope_authorization() -> None:
    authorizer = _Authorizer(True)
    service = ConfigAdmissionService(authorizer=authorizer, policy_admission=_admit)

    result = asyncio.run(service.admit(actor=ACTOR, source=CONFIG_SOURCE, source_format="json"))

    assert result == ConfigAdmissionAccepted(admitted_config_epoch())
    assert authorizer.calls == [(ACTOR, SCOPE)]


def test_invalid_source_cannot_reach_scope_authorization() -> None:
    authorizer = _Authorizer(True)
    service = ConfigAdmissionService(authorizer=authorizer, policy_admission=_admit)

    result = asyncio.run(service.admit(actor=ACTOR, source=b"{}", source_format="json"))

    assert isinstance(result, ConfigAdmissionInvalid)
    assert result.diagnostics
    assert authorizer.calls == []


def test_valid_source_is_forbidden_when_its_scope_is_not_authorized() -> None:
    authorizer = _Authorizer(False)
    service = ConfigAdmissionService(authorizer=authorizer, policy_admission=_admit)

    result = asyncio.run(service.admit(actor=ACTOR, source=CONFIG_SOURCE, source_format="json"))

    assert isinstance(result, ConfigAdmissionForbidden)
    assert authorizer.calls == [(ACTOR, SCOPE)]


def test_bounded_admission_unavailability_is_a_closed_outcome() -> None:
    async def unavailable(
        source: bytes,
        source_format: PolicySourceFormat,
    ) -> PolicyAdmissionResult:
        del source, source_format
        raise PolicyAdmissionUnavailable

    authorizer = _Authorizer(True)
    service = ConfigAdmissionService(authorizer=authorizer, policy_admission=unavailable)

    result = asyncio.run(service.admit(actor=ACTOR, source=CONFIG_SOURCE, source_format="json"))

    assert isinstance(result, ConfigAdmissionUnavailable)
    assert authorizer.calls == []


def test_invalid_admission_outcome_requires_at_least_one_diagnostic() -> None:
    with pytest.raises(ValueError, match="requires diagnostics"):
        ConfigAdmissionInvalid(())
    with pytest.raises(TypeError, match="exact policy diagnostics"):
        ConfigAdmissionInvalid(cast(tuple[PolicyDiagnostic, ...], (object(),)))
    with pytest.raises(TypeError, match="exact policy diagnostics"):
        ConfigAdmissionInvalid(cast(tuple[PolicyDiagnostic, ...], [object()]))
