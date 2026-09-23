"""Pure immutable revision-declaration contracts and transition algebra."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile

type TransitionKind = Literal["bootstrap", "expand", "contract"]


class CompatibilityContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True, order=True)
class CapabilityDeclaration:
    capability_id: str
    descriptor_hash: str

    def __post_init__(self) -> None:
        if len(self.descriptor_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.descriptor_hash
        ):
            raise CompatibilityContractError(
                "database_capability_descriptor_invalid",
                "capability descriptor hash must be a lowercase SHA-256 hex digest",
            )


@dataclass(frozen=True, slots=True)
class RevisionDeclaration:
    generation: int
    lineage_id: str
    revision_id: str
    parent_revision_id: str | None
    transition_kind: TransitionKind
    protocol_version: int
    capabilities: tuple[CapabilityDeclaration, ...]
    declaration_hash: str

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 1:
            raise CompatibilityContractError(
                "database_declaration_generation_invalid",
                "declaration generation must be a positive integer",
            )
        if self.transition_kind not in {"bootstrap", "expand", "contract"}:
            raise CompatibilityContractError(
                "database_declaration_transition_invalid",
                "declaration transition kind is invalid",
            )
        if not self.capabilities:
            raise CompatibilityContractError(
                "database_declaration_capabilities_empty",
                "declaration capabilities must be non-empty",
            )
        if self.capabilities != _ordered_unique_capabilities(self.capabilities):
            raise CompatibilityContractError(
                "database_declaration_capabilities_unordered",
                "declaration capabilities must be unique and ordered by UTF-8 bytes",
            )

    def projection(self) -> dict[str, object]:
        return {
            "generation": self.generation,
            "lineageId": self.lineage_id,
            "revisionId": self.revision_id,
            "parentRevisionId": self.parent_revision_id,
            "transitionKind": self.transition_kind,
            "protocolVersion": self.protocol_version,
            "providedCapabilities": [
                {
                    "capabilityId": capability.capability_id,
                    "descriptorHash": capability.descriptor_hash,
                }
                for capability in self.capabilities
            ],
        }


def declaration_hash(
    *,
    generation: int,
    lineage_id: str,
    revision_id: str,
    parent_revision_id: str | None,
    transition_kind: TransitionKind,
    protocol_version: int,
    capabilities: tuple[CapabilityDeclaration, ...],
) -> str:
    projection = {
        "generation": generation,
        "lineageId": lineage_id,
        "revisionId": revision_id,
        "parentRevisionId": parent_revision_id,
        "transitionKind": transition_kind,
        "protocolVersion": protocol_version,
        "providedCapabilities": [
            {
                "capabilityId": capability.capability_id,
                "descriptorHash": capability.descriptor_hash,
            }
            for capability in capabilities
        ],
    }
    return sha256(b"ci-database-revision/v1\0" + canonical_json(projection)).hexdigest()


def build_declaration(
    profile: CompatibilityProfile,
    *,
    generation: int,
    revision_id: str,
    parent_revision_id: str | None,
    transition_kind: TransitionKind,
    capabilities: tuple[CapabilityDeclaration, ...],
) -> RevisionDeclaration:
    _validate_revision_id(profile, revision_id, "revision")
    if parent_revision_id is not None:
        _validate_revision_id(profile, parent_revision_id, "parent revision")
    canonical_capabilities = _ordered_unique_capabilities(capabilities)
    if len(canonical_capabilities) > profile.maximum_capabilities_per_revision:
        raise CompatibilityContractError(
            "database_declaration_bounds_exceeded",
            "declaration exceeds the capability bound",
        )
    for capability in canonical_capabilities:
        _validate_capability(profile, capability)
    digest = declaration_hash(
        generation=generation,
        lineage_id=profile.lineage_id,
        revision_id=revision_id,
        parent_revision_id=parent_revision_id,
        transition_kind=transition_kind,
        protocol_version=profile.protocol_version,
        capabilities=canonical_capabilities,
    )
    declaration = RevisionDeclaration(
        generation=generation,
        lineage_id=profile.lineage_id,
        revision_id=revision_id,
        parent_revision_id=parent_revision_id,
        transition_kind=transition_kind,
        protocol_version=profile.protocol_version,
        capabilities=canonical_capabilities,
        declaration_hash=digest,
    )
    validate_declaration(profile, declaration)
    return declaration


def validate_declaration(profile: CompatibilityProfile, declaration: RevisionDeclaration) -> None:
    _validate_revision_id(profile, declaration.revision_id, "revision")
    if declaration.parent_revision_id is not None:
        _validate_revision_id(profile, declaration.parent_revision_id, "parent revision")
    if declaration.lineage_id != profile.lineage_id:
        raise CompatibilityContractError(
            "database_declaration_lineage_invalid",
            "declaration lineage does not match the admitted profile",
        )
    if declaration.protocol_version != profile.protocol_version:
        raise CompatibilityContractError(
            "database_declaration_protocol_invalid",
            "declaration protocol version does not match the admitted profile",
        )
    if len(declaration.capabilities) > profile.maximum_capabilities_per_revision:
        raise CompatibilityContractError(
            "database_declaration_bounds_exceeded",
            "declaration exceeds the capability bound",
        )
    for capability in declaration.capabilities:
        _validate_capability(profile, capability)
    expected_hash = declaration_hash(
        generation=declaration.generation,
        lineage_id=declaration.lineage_id,
        revision_id=declaration.revision_id,
        parent_revision_id=declaration.parent_revision_id,
        transition_kind=declaration.transition_kind,
        protocol_version=declaration.protocol_version,
        capabilities=declaration.capabilities,
    )
    if declaration.declaration_hash != expected_hash:
        raise CompatibilityContractError(
            "database_declaration_hash_mismatch",
            "declaration hash does not match its canonical projection",
        )
    if declaration.transition_kind == "bootstrap":
        if declaration.generation != 1 or declaration.parent_revision_id is not None:
            raise CompatibilityContractError(
                "database_declaration_bootstrap_invalid",
                "bootstrap must use generation one and no parent revision",
            )
    elif declaration.generation == 1 or declaration.parent_revision_id is None:
        raise CompatibilityContractError(
            "database_declaration_successor_invalid",
            "a successor must have a generation above one and an exact parent revision",
        )


def validate_successor(
    profile: CompatibilityProfile,
    previous: RevisionDeclaration | None,
    candidate: RevisionDeclaration,
) -> None:
    validate_declaration(profile, candidate)
    if previous is None:
        if candidate.transition_kind != "bootstrap":
            raise CompatibilityContractError(
                "database_declaration_bootstrap_required",
                "the first declaration must be a bootstrap",
            )
        return
    validate_declaration(profile, previous)
    if candidate.transition_kind == "bootstrap":
        raise CompatibilityContractError(
            "database_declaration_duplicate_bootstrap",
            "a successor cannot be a bootstrap",
        )
    if candidate.generation != previous.generation + 1:
        raise CompatibilityContractError(
            "database_declaration_generation_gap",
            "successor generation must increment by one",
        )
    if candidate.parent_revision_id != previous.revision_id:
        raise CompatibilityContractError(
            "database_declaration_parent_invalid",
            "successor parent must be the immediately previous revision",
        )
    previous_by_id = {item.capability_id: item.descriptor_hash for item in previous.capabilities}
    candidate_by_id = {item.capability_id: item.descriptor_hash for item in candidate.capabilities}
    retained_ids = set(previous_by_id).intersection(candidate_by_id)
    if any(previous_by_id[item] != candidate_by_id[item] for item in retained_ids):
        raise CompatibilityContractError(
            "database_capability_descriptor_rewritten",
            "a retained capability identifier cannot change descriptor hash",
        )
    previous_ids = set(previous_by_id)
    candidate_ids = set(candidate_by_id)
    if candidate.transition_kind == "expand":
        if not previous_ids < candidate_ids:
            raise CompatibilityContractError(
                "database_declaration_expand_invalid",
                "an expand transition must add at least one capability and remove none",
            )
    elif candidate.transition_kind == "contract" and not candidate_ids < previous_ids:
        raise CompatibilityContractError(
            "database_declaration_contract_invalid",
            "a contract transition must remove at least one capability and add none",
        )


def assert_exact_replay(
    stored: RevisionDeclaration,
    proposed: RevisionDeclaration,
) -> None:
    if stored != proposed:
        raise CompatibilityContractError(
            "database_declaration_replay_conflict",
            "an existing revision declaration differs from the proposed replay",
        )


def required_capabilities(
    profile: CompatibilityProfile,
    capabilities: tuple[CapabilityDeclaration, ...],
) -> tuple[CapabilityDeclaration, ...]:
    canonical_capabilities = _ordered_unique_capabilities(capabilities)
    if not canonical_capabilities:
        raise CompatibilityContractError(
            "database_required_capabilities_empty",
            "a schema-dependent operation requires at least one capability",
        )
    if len(canonical_capabilities) > profile.maximum_required_capabilities_per_operation:
        raise CompatibilityContractError(
            "database_required_capabilities_bounds_exceeded",
            "operation exceeds the required capability bound",
        )
    for capability in canonical_capabilities:
        _validate_capability(profile, capability)
    return canonical_capabilities


def capabilities_cover(
    required: tuple[CapabilityDeclaration, ...],
    provided: tuple[CapabilityDeclaration, ...],
) -> bool:
    provided_by_id = {item.capability_id: item.descriptor_hash for item in provided}
    return all(provided_by_id.get(item.capability_id) == item.descriptor_hash for item in required)


def _ordered_unique_capabilities(
    capabilities: tuple[CapabilityDeclaration, ...],
) -> tuple[CapabilityDeclaration, ...]:
    ordered = tuple(sorted(capabilities, key=lambda item: item.capability_id.encode("utf-8")))
    if len({item.capability_id for item in ordered}) != len(ordered):
        raise CompatibilityContractError(
            "database_capability_identifier_duplicate",
            "capability identifiers must be unique",
        )
    return ordered


def _validate_revision_id(profile: CompatibilityProfile, revision_id: str, label: str) -> None:
    if profile.revision_id_pattern.fullmatch(revision_id) is None:
        raise CompatibilityContractError(
            "database_declaration_revision_invalid",
            f"{label} does not match the admitted revision identifier pattern",
        )


def _validate_capability(profile: CompatibilityProfile, capability: CapabilityDeclaration) -> None:
    if len(capability.capability_id.encode("utf-8")) > profile.maximum_capability_id_bytes:
        raise CompatibilityContractError(
            "database_capability_identifier_too_large",
            "capability identifier exceeds the profile byte limit",
        )
    if profile.capability_id_pattern.fullmatch(capability.capability_id) is None:
        raise CompatibilityContractError(
            "database_capability_identifier_invalid",
            "capability identifier does not match the admitted pattern",
        )
