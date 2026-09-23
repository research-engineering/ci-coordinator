from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from ci_coordinator.kernel import hash_object
from ci_coordinator.validation_contract import (
    ExecutableWitness,
    ExecutionProfile,
    ShardingPolicy,
    ValidationCatalog,
    ValidationDepth,
    ValidationObligation,
)


def _profile(profile_id: str = "python") -> ExecutionProfile:
    return ExecutionProfile(
        profile_id=profile_id,
        runner_profile_id="ubuntu-24-04",
        permission_profile_id="contents-read",
        credential_profile_id="none",
        fixture_profile_id="none",
        service_profile_ids=(),
        capacity_class_id="standard",
        sharding_policy=ShardingPolicy(
            max_shards=8,
            max_parallel=4,
            max_items_per_shard=512,
            setup_seconds_per_shard=10,
        ),
    )


def _witness(
    witness_id: str = "python-tests",
    *,
    profile_id: str = "python",
) -> ExecutableWitness:
    return ExecutableWitness(
        witness_id=witness_id,
        execution_profile_id=profile_id,
        supported_depths=("targeted", "full"),
    )


def _obligation(witness_id: str = "python-tests") -> ValidationObligation:
    return ValidationObligation(
        obligation_id="backend-correctness",
        responsibility_paths=("backend/**",),
        responsibility_risk_classes=("backend",),
        required_witness_ids=(witness_id,),
        default_depth="targeted",
        full_depth="full",
        omit_allowed=True,
    )


def _catalog() -> ValidationCatalog:
    return ValidationCatalog(
        obligations=(_obligation(),),
        witnesses=(_witness(),),
        execution_profiles=(_profile(),),
    )


def test_catalog_closes_obligation_witness_and_profile_references() -> None:
    catalog = _catalog()

    assert catalog.catalog_hash == catalog.catalog_hash
    assert catalog.catalog_hash == hash_object(catalog.to_identity_mapping())
    assert catalog.obligations[0].obligation_id == "backend-correctness"


def test_catalog_hash_is_computed_once_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def count_hash(value: object) -> str:
        nonlocal calls
        calls += 1
        return hash_object(value)

    monkeypatch.setattr(
        "ci_coordinator.validation_contract.catalog.hash_object",
        count_hash,
    )
    catalog = _catalog()

    expected = catalog.catalog_hash
    assert all(catalog.catalog_hash == expected for _ in range(100))
    assert calls == 1


@pytest.mark.parametrize(
    "candidate",
    [
        lambda: replace(
            _catalog(),
            obligations=cast(tuple[ValidationObligation, ...], [_obligation()]),
        ),
        lambda: replace(
            _catalog(),
            witnesses=cast(tuple[ExecutableWitness, ...], [_witness()]),
        ),
        lambda: replace(
            _catalog(),
            execution_profiles=cast(tuple[ExecutionProfile, ...], [_profile()]),
        ),
    ],
)
def test_catalog_rejects_mutable_top_level_collections(
    candidate: Callable[[], ValidationCatalog],
) -> None:
    with pytest.raises(TypeError, match="exact tuple"):
        candidate()


@pytest.mark.parametrize(
    "candidate",
    [
        lambda: replace(
            _catalog(),
            obligations=(cast(ValidationObligation, object()),),
        ),
        lambda: replace(
            _catalog(),
            witnesses=(cast(ExecutableWitness, object()),),
        ),
        lambda: replace(
            _catalog(),
            execution_profiles=(cast(ExecutionProfile, object()),),
        ),
    ],
)
def test_catalog_rejects_non_model_members(
    candidate: Callable[[], ValidationCatalog],
) -> None:
    with pytest.raises(TypeError, match="must contain exact"):
        candidate()


@pytest.mark.parametrize(
    "candidate",
    [
        lambda: replace(
            _obligation(),
            responsibility_paths=cast(tuple[str, ...], ["backend/**"]),
        ),
        lambda: replace(
            _obligation(),
            responsibility_risk_classes=cast(tuple[str, ...], ["backend"]),
        ),
        lambda: replace(
            _obligation(),
            required_witness_ids=cast(tuple[str, ...], ["python-tests"]),
        ),
        lambda: replace(
            _witness(),
            supported_depths=cast(
                tuple[ValidationDepth, ...],
                ["targeted", "full"],
            ),
        ),
        lambda: replace(
            _profile(),
            service_profile_ids=cast(tuple[str, ...], ["postgresql"]),
        ),
    ],
)
def test_catalog_models_reject_mutable_identity_collections(
    candidate: Callable[[], object],
) -> None:
    with pytest.raises(TypeError, match="exact tuple"):
        candidate()


def test_execution_profile_rejects_a_duck_typed_sharding_policy() -> None:
    with pytest.raises(TypeError, match="exact ShardingPolicy"):
        replace(
            _profile(),
            sharding_policy=cast(ShardingPolicy, object()),
        )


def test_catalog_models_reject_identity_primitive_subclasses() -> None:
    class FloatSubclass(float):
        pass

    class StringSubclass(str):
        pass

    with pytest.raises(ValueError, match="finite number"):
        replace(_profile().sharding_policy, cpu_weight=FloatSubclass(1))
    with pytest.raises(ValueError, match="not admitted"):
        replace(
            _witness(),
            supported_depths=(
                cast(ValidationDepth, StringSubclass("targeted")),
                "full",
            ),
        )


@pytest.mark.parametrize(
    ("obligations", "witnesses", "profiles", "message"),
    [
        ((_obligation("missing"),), (_witness(),), (_profile(),), "every witness"),
        ((_obligation(),), (_witness(profile_id="missing"),), (_profile(),), "profile"),
        (
            (_obligation(),),
            (replace(_witness(), supported_depths=("targeted",)),),
            (_profile(),),
            "does not support",
        ),
    ],
)
def test_catalog_rejects_open_or_incompatible_references(
    obligations: tuple[ValidationObligation, ...],
    witnesses: tuple[ExecutableWitness, ...],
    profiles: tuple[ExecutionProfile, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ValidationCatalog(obligations, witnesses, profiles)


def test_only_omittable_obligations_require_a_responsibility_surface() -> None:
    mandatory = replace(
        _obligation(),
        responsibility_paths=(),
        responsibility_risk_classes=(),
        omit_allowed=False,
    )

    assert mandatory.responsibility_paths == ()
    with pytest.raises(ValueError, match="responsibility surface"):
        replace(mandatory, omit_allowed=True)


@pytest.mark.parametrize(
    "candidate",
    [
        lambda: replace(_profile().sharding_policy, max_shards=257),
        lambda: replace(_profile().sharding_policy, max_parallel=9),
        lambda: replace(
            _profile().sharding_policy,
            setup_seconds_per_shard=float("inf"),
        ),
        lambda: replace(
            _profile().sharding_policy,
            cpu_weight=0,
            wall_weight=0,
            operator_weight=0,
        ),
    ],
)
def test_sharding_policy_rejects_unbounded_or_vacuous_values(
    candidate: Callable[[], ShardingPolicy],
) -> None:
    with pytest.raises(ValueError):
        candidate()


@pytest.mark.parametrize(("count", "admitted"), [(16, True), (17, False)])
def test_execution_profile_bounds_service_profile_ids(
    count: int,
    admitted: bool,
) -> None:
    service_profile_ids = tuple(f"service-{index:02d}" for index in range(count))

    if admitted:
        assert (
            replace(
                _profile(),
                service_profile_ids=service_profile_ids,
            ).service_profile_ids
            == service_profile_ids
        )
    else:
        with pytest.raises(ValueError, match="admitted maximum"):
            replace(_profile(), service_profile_ids=service_profile_ids)
