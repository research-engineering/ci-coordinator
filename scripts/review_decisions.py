from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from scripts.proofkit_common import parse_json_object
from scripts.repository_source_admission import read_bounded_repository_bytes

REGISTER_PATH: Final = PurePosixPath("docs/decisions/review-decisions.v1.json")
_MAX_REGISTER_BYTES: Final = 128 * 1024
_MAX_OWNER_BYTES: Final = 512 * 1024
type _Text = Annotated[str, Field(min_length=1, max_length=2_000, pattern=r"\S")]
type _Texts = Annotated[list[_Text], Field(min_length=1, max_length=16)]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class _OwnerReference(_ClosedModel):
    path: Annotated[str, Field(min_length=1, max_length=300)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class _Decision(_ClosedModel):
    decision_id: Annotated[str, Field(pattern=r"^RD-[0-9]{3}$")] = Field(alias="decisionId")
    kind: Literal["owner-choice", "conditional-tradeoff"]
    rejected_argument: _Text = Field(alias="rejectedArgument")
    scope: _Text
    owner_references: Annotated[list[_OwnerReference], Field(min_length=1, max_length=8)] = Field(
        alias="ownerReferences"
    )
    assumptions: _Texts
    protected_observations: _Texts = Field(alias="protectedObservations")
    simplest_alternative: _Text = Field(alias="simplestAlternative")
    rationale: _Text
    accepted_cost: _Text = Field(alias="acceptedCost")
    falsifiers: _Texts
    revision_triggers: _Texts = Field(alias="revisionTriggers")
    residual_review: _Text = Field(alias="residualReview")

    @model_validator(mode="after")
    def unique_owners(self) -> Self:
        paths = [owner.path for owner in self.owner_references]
        if len(paths) != len(set(paths)):
            raise ValueError("decision owner references must be unique")
        return self


class _Register(_ClosedModel):
    schema_version: Annotated[int, Field(ge=1, le=1)] = Field(alias="schemaVersion")
    register_id: Literal["ci-coordinator.review-decisions/v1"] = Field(alias="registerId")
    decisions: Annotated[list[_Decision], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def ordered_unique_ids(self) -> Self:
        ids = [decision.decision_id for decision in self.decisions]
        if ids != sorted(set(ids)):
            raise ValueError("decision IDs must be sorted and unique")
        return self


@dataclass(frozen=True, slots=True)
class DecisionReferenceState:
    decision_id: str
    changed_owner_paths: tuple[str, ...]

    @property
    def state(self) -> Literal["reference-current", "review-required"]:
        return "review-required" if self.changed_owner_paths else "reference-current"


def validate_decision_register(repo_root: Path) -> tuple[DecisionReferenceState, ...]:
    source = read_bounded_repository_bytes(
        repo_root, REGISTER_PATH, maximum_bytes=_MAX_REGISTER_BYTES
    )
    try:
        register = _Register.model_validate(
            parse_json_object(source.decode("utf-8", errors="strict"), str(REGISTER_PATH))
        )
    except ValidationError as error:
        locations = [
            ".".join(map(str, item["loc"]))
            for item in error.errors(include_url=False, include_input=False)[:8]
        ]
        raise ValueError("invalid decision register fields: " + ", ".join(locations)) from error
    owners: dict[str, str] = {}
    for decision in register.decisions:
        for owner in decision.owner_references:
            if owner.path not in owners:
                path = PurePosixPath(owner.path)
                if path.as_posix() != owner.path:
                    raise ValueError("decision owner path must be canonical")
                payload = read_bounded_repository_bytes(
                    repo_root, path, maximum_bytes=_MAX_OWNER_BYTES
                )
                owners[owner.path] = hashlib.sha256(payload).hexdigest()
    return tuple(
        DecisionReferenceState(
            decision.decision_id,
            tuple(
                owner.path
                for owner in decision.owner_references
                if owners[owner.path] != owner.sha256
            ),
        )
        for decision in register.decisions
    )
