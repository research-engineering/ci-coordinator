"""Closed validation catalog with cross-reference invariants."""

from __future__ import annotations

from dataclasses import dataclass, field

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.validation_contract.model import (
    ExecutableWitness,
    ExecutionProfile,
    ValidationObligation,
)


@dataclass(frozen=True, slots=True)
class ValidationCatalog:
    obligations: tuple[ValidationObligation, ...]
    witnesses: tuple[ExecutableWitness, ...]
    execution_profiles: tuple[ExecutionProfile, ...]
    _catalog_hash: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_exact_catalog_members(
            self.obligations,
            member_type=ValidationObligation,
            field_name="obligations",
        )
        _require_exact_catalog_members(
            self.witnesses,
            member_type=ExecutableWitness,
            field_name="witnesses",
        )
        _require_exact_catalog_members(
            self.execution_profiles,
            member_type=ExecutionProfile,
            field_name="execution_profiles",
        )
        _require_canonical_objects(
            self.obligations,
            identities=tuple(item.obligation_id for item in self.obligations),
            field_name="obligations",
        )
        _require_canonical_objects(
            self.witnesses,
            identities=tuple(item.witness_id for item in self.witnesses),
            field_name="witnesses",
        )
        _require_canonical_objects(
            self.execution_profiles,
            identities=tuple(item.profile_id for item in self.execution_profiles),
            field_name="execution_profiles",
        )
        self._require_closed_references()
        object.__setattr__(self, "_catalog_hash", hash_object(self.to_identity_mapping()))

    @property
    def catalog_hash(self) -> str:
        return self._catalog_hash

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "obligations": [item.to_identity_mapping() for item in self.obligations],
            "witnesses": [item.to_identity_mapping() for item in self.witnesses],
            "executionProfiles": [item.to_identity_mapping() for item in self.execution_profiles],
        }

    def _require_closed_references(self) -> None:
        witnesses = {item.witness_id: item for item in self.witnesses}
        profiles = {item.profile_id for item in self.execution_profiles}
        referenced_witnesses = {
            witness_id
            for obligation in self.obligations
            for witness_id in obligation.required_witness_ids
        }
        if referenced_witnesses != set(witnesses):
            raise ValueError(
                "every witness must be referenced by an obligation exactly by identity"
            )
        referenced_profiles = {item.execution_profile_id for item in self.witnesses}
        if referenced_profiles != profiles:
            raise ValueError("every execution profile must be referenced by a witness")
        for obligation in self.obligations:
            required_depths = {obligation.default_depth, obligation.full_depth}
            for witness_id in obligation.required_witness_ids:
                witness = witnesses[witness_id]
                if not required_depths.issubset(witness.supported_depths):
                    raise ValueError(f"witness {witness_id!r} does not support obligation depths")


def _require_canonical_objects(
    values: tuple[object, ...],
    *,
    identities: tuple[str, ...],
    field_name: str,
) -> None:
    if not values:
        raise ValueError(f"{field_name} must not be empty")
    if tuple(sorted(set(identities), key=utf16_sort_key)) != identities:
        raise ValueError(f"{field_name} must be canonical")


def _require_exact_catalog_members(
    values: object,
    *,
    member_type: type[object],
    field_name: str,
) -> None:
    if type(values) is not tuple:
        raise TypeError(f"{field_name} must be an exact tuple")
    if any(type(value) is not member_type for value in values):
        raise TypeError(f"{field_name} must contain exact {member_type.__name__} values")
