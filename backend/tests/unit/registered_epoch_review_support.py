"""Shared fakes for registered-epoch review, attestation and activation witnesses."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from repository_activation_support import ACTOR, NOW, PROFILE_DIGEST, REVIEWER, SCOPE

from ci_coordinator.config_control import (
    RepositoryScope,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_epochs import (
    ActiveConfigEpoch,
    ActiveConfigEpochSnapshot,
    ConfigEpochActivationApplied,
    ConfigEpochActivationRecord,
    PreparedConfigEpochActivation,
)
from ci_coordinator.control_plane_identity import (
    GitHubReviewerEvidence,
    GitHubReviewerPrincipal,
    RegisteredEpochStepUpBinding,
)
from ci_coordinator.proposal_review import (
    AttestedConfigActivationResult,
    PreparedRegisteredEpochReview,
    ProposalReviewStoreUnavailable,
    RegisteredEpochActivationAuthority,
    RegisteredEpochAttestationEvidence,
    RegisteredEpochAttestationTransaction,
    RegisteredEpochLookup,
    RegisteredEpochRegistrant,
    RegisteredEpochReviewAttestationConflict,
    RegisteredEpochReviewCommand,
    RegisteredEpochReviewCreated,
    RegisteredEpochReviewDraft,
    RegisteredEpochReviewRecord,
    RegisteredEpochReviewResolution,
    RegisteredEpochReviewWriteResult,
    RegisteredEpochSubject,
    RepositoryAttestationRegistered,
    RepositoryAttestationRegistration,
    compare_policy_drafts,
)
from ci_coordinator.workflow_discovery import (
    RepositoryIdentity,
    RepositoryWorkflowSnapshot,
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryOutcome,
    WorkflowDiscoveryService,
    WorkflowSource,
)
from ci_coordinator.workflow_discovery.source import git_blob_sha1

REGISTRANT = RegisteredEpochRegistrant(
    operation_id="register-1",
    actor="keycloak-workload:v1:" + "7" * 64,
    audit_event_id="audit_" + "8" * 32,
)


def discovered_inventory(
    *,
    revision: str = "a" * 40,
    shape: str = "complete",
) -> WorkflowDiscoveryCompleted:
    """Discover one real snapshot whose report is complete, partial or graph-open."""
    files = {
        ".github/workflows/ci.yml": (
            b"name: CI\non:\n  push:\njobs:\n  full:\n    runs-on: ubuntu-latest\n"
            b"    steps:\n      - run: python -m pytest\n"
        )
    }
    if shape == "incomplete":
        files[".github/workflows/broken.yml"] = b"name: [unterminated\n"
    elif shape == "open_graph":
        files[".github/workflows/caller.yml"] = (
            b"name: Caller\non:\n  push:\njobs:\n  call:\n"
            b"    uses: ./.github/workflows/missing.yml\n"
        )
    elif shape == "changed":
        files[".github/workflows/lint.yml"] = (
            b"name: Lint\non:\n  push:\njobs:\n  lint:\n    runs-on: ubuntu-latest\n"
            b"    steps:\n      - run: ruff check\n"
        )
    elif shape != "complete":
        raise ValueError(shape)
    snapshot = RepositoryWorkflowSnapshot.create(
        repository=RepositoryIdentity(SCOPE, "example", "repo", "master"),
        revision=revision,
        sources=tuple(
            WorkflowSource(path, git_blob_sha1(content), len(content), content)
            for path, content in files.items()
        ),
    )
    result = asyncio.run(
        WorkflowDiscoveryService(authorizer=_AllowScope(), reader=_SnapshotReader(snapshot))(
            actor=ACTOR,
            scope=SCOPE,
            revision=None,
        )
    )
    assert isinstance(result, WorkflowDiscoveryCompleted)
    report = result.report
    assert (report.complete, report.local_graph_closed) == {
        "complete": (True, True),
        "changed": (True, True),
        "incomplete": (False, report.local_graph_closed),
        "open_graph": (True, False),
    }[shape]
    return result


def admitted_draft(
    *,
    installation_id: int = SCOPE.installation_id,
    repository_id: int = SCOPE.repository_id,
    signal: str = "CI",
) -> ValidatedEpochDraft:
    """Admit one custom policy as a standalone registration would."""
    source = (
        b'{"schemaVersion":"ci-repository-policy/v1","repository":'
        b'{"installationId":%d,"repositoryId":%d,"owner":"example","name":"repo",'
        b'"defaultBranch":"master","dynamicCi":null,"rules":[{"name":"main",'
        b'"on":{"event":"push","branches":["master"]},"mode":"observe",'
        b'"timing":{"expectedSignalTimeoutSeconds":3600,'
        b'"absenceVerificationWindowSeconds":300,"absencePollLookbackSeconds":3600,'
        b'"lateFindingWindowSeconds":86400,"mutableDecisionWindowSeconds":300},'
        b'"expectedSignals":[{"kind":"workflow","name":"%s",'
        b'"workflowFile":"ci.yml","source":"native",'
        b'"requiredConclusion":"success","required":true}],'
        b'"omittedSignals":[]}]}}'
    ) % (installation_id, repository_id, signal.encode())
    result = admit_policy_document(source, "json")
    assert type(result) is ValidatedEpochDraft
    return result


def registered_subject(draft: ValidatedEpochDraft) -> RegisteredEpochSubject:
    return RegisteredEpochSubject(draft=draft, registrant=REGISTRANT)


def registered_command(
    draft: ValidatedEpochDraft,
    *,
    actor: str = ACTOR,
    operation_id: str = "registered-review-1",
    source_hash: str | None = None,
    expected_active: ActiveConfigEpoch | None = None,
) -> RegisteredEpochReviewCommand:
    return RegisteredEpochReviewCommand(
        scope=SCOPE,
        operation_id=operation_id,
        target_epoch_id=draft.epoch_id,
        expected_source_hash=source_hash or draft.source_hash,
        expected_active=expected_active,
        actor=actor,
    )


def registered_attestation(
    command: RegisteredEpochReviewCommand,
    *,
    revision: str,
    inventory_digest: str,
    reviewer: GitHubReviewerEvidence = REVIEWER,
    issued_at: datetime = NOW,
) -> RegisteredEpochAttestationEvidence:
    expiry = issued_at + timedelta(minutes=5)
    active = command.expected_active
    return RegisteredEpochAttestationEvidence(
        RegisteredEpochAttestationTransaction(
            b"9" * 32,
            RegisteredEpochStepUpBinding(
                session_handle_digest=b"4" * 32,
                initiating_actor=command.actor,
                scope=command.scope,
                operation_id=command.operation_id,
                target_epoch_id=command.target_epoch_id,
                source_hash=command.expected_source_hash,
                revision=revision,
                inventory_digest=inventory_digest,
                expected_active_epoch_id=None if active is None else active.epoch_id,
                expected_active_revision=None if active is None else active.revision,
                authority_profile_digest=PROFILE_DIGEST,
                issued_at=issued_at,
                expires_at=expiry,
            ),
        ),
        GitHubReviewerPrincipal(evidence=reviewer, observed_at=issued_at, expires_at=expiry),
    )


def registered_review_record(
    completed: WorkflowDiscoveryCompleted,
    draft: ValidatedEpochDraft,
    *,
    actor: str = ACTOR,
    base: ActiveConfigEpochSnapshot | None = None,
) -> RegisteredEpochReviewRecord:
    command = registered_command(
        draft,
        actor=actor,
        expected_active=None if base is None else base.active,
    )
    report = completed.report
    return RegisteredEpochReviewRecord.from_draft(
        RegisteredEpochReviewDraft(
            command=command,
            provider_revision=report.revision,
            inventory_digest=report.inventory_digest,
            subject=registered_subject(draft),
            semantic_diff=compare_policy_drafts(None if base is None else base.draft, draft),
        ),
        attestation=registered_attestation(
            command,
            revision=report.revision,
            inventory_digest=report.inventory_digest,
        ),
        audit_event_id="audit_" + "5" * 32,
        audit_input_hash="6" * 64,
    )


@dataclass
class RegisteredEpochStore:
    """In-memory registered-epoch review, attestation and activation store."""

    lookup: RegisteredEpochLookup = None
    now: datetime = NOW
    active: ActiveConfigEpochSnapshot | None = None
    resolution: RegisteredEpochReviewResolution = None
    registration: RepositoryAttestationRegistration = field(
        default_factory=RepositoryAttestationRegistered
    )
    candidate: RegisteredEpochReviewRecord | None = None
    activation: AttestedConfigActivationResult | None = None
    unavailable_at: str | None = None
    pending: RegisteredEpochAttestationTransaction | None = None
    calls: list[object] = field(default_factory=list)

    def _maybe_fail(self, stage: str) -> None:
        if self.unavailable_at == stage:
            raise ProposalReviewStoreUnavailable(stage)

    async def current_time(self) -> datetime:
        self.calls.append("current_time")
        self._maybe_fail("time")
        return self.now

    async def register_attestation(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> RepositoryAttestationRegistration:
        self.calls.append(("register", transaction))
        self._maybe_fail("register")
        if isinstance(self.registration, RepositoryAttestationRegistered):
            self.pending = transaction
        return self.registration

    async def attestation_is_pending(
        self,
        transaction: RegisteredEpochAttestationTransaction,
    ) -> bool:
        self.calls.append(("pending", transaction))
        self._maybe_fail("pending")
        return self.pending == transaction

    async def retire_attestation(self, transaction: RegisteredEpochAttestationTransaction) -> None:
        self.calls.append(("retire", transaction))
        if self.pending == transaction:
            self.pending = None

    async def resolve_operation(
        self,
        command: RegisteredEpochReviewCommand,
    ) -> RegisteredEpochReviewResolution:
        self.calls.append(("resolve", command))
        self._maybe_fail("resolve")
        return self.resolution

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        self.calls.append(("active", scope))
        self._maybe_fail("active")
        return self.active

    async def load_registered_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> RegisteredEpochLookup:
        self.calls.append(("epoch", scope, epoch_id))
        self._maybe_fail("epoch")
        if isinstance(self.lookup, RegisteredEpochSubject) and (
            self.lookup.draft.scope != scope or self.lookup.draft.epoch_id != epoch_id
        ):
            return None
        return self.lookup

    async def accept(
        self,
        prepared: PreparedRegisteredEpochReview,
    ) -> RegisteredEpochReviewWriteResult:
        self.calls.append(("accept", prepared))
        self._maybe_fail("accept")
        if self.pending != prepared.attestation.transaction:
            return RegisteredEpochReviewAttestationConflict()
        self.pending = None
        return RegisteredEpochReviewCreated(
            RegisteredEpochReviewRecord.from_draft(
                prepared.draft,
                attestation=prepared.attestation,
                audit_event_id="audit_" + "5" * 32,
                audit_input_hash=prepared.audit_input_hash,
            )
        )

    async def load_activation_candidate(
        self,
        *,
        scope: RepositoryScope,
        target_epoch_id: str,
        expected_source_hash: str,
        review_operation_id: str,
    ) -> RegisteredEpochReviewRecord | None:
        self.calls.append(("candidate", scope, target_epoch_id, expected_source_hash))
        self._maybe_fail("candidate")
        return self.candidate

    async def activate_config(
        self,
        prepared: PreparedConfigEpochActivation,
        authority: RegisteredEpochActivationAuthority,
    ) -> AttestedConfigActivationResult:
        self.calls.append(("activate", prepared.command, authority))
        if self.activation is not None:
            return self.activation
        command = prepared.command
        return ConfigEpochActivationApplied(
            ConfigEpochActivationRecord(
                scope=command.scope,
                operation_id=command.operation_id,
                expected_revision=None,
                previous=None,
                active=ActiveConfigEpoch(command.scope, command.target_epoch_id, 1),
                audit_event_id="audit_" + "a" * 32,
                audit_input_hash=prepared.audit_input_hash,
            )
        )


@dataclass
class ReviewerProvider:
    """GitHub reviewer OAuth fake that records every provider effect."""

    exchange_result: GitHubReviewerEvidence | Exception = REVIEWER
    state: str | None = None
    authorization_calls: list[tuple[str, str]] = field(default_factory=list)
    exchange_calls: list[tuple[str, str, RepositoryScope]] = field(default_factory=list)

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        self.state = state
        self.authorization_calls.append((state, code_challenge))
        return f"https://github.com/login/oauth/authorize?state={state}"

    async def exchange_and_resolve(
        self,
        *,
        code: str,
        code_verifier: str,
        scope: RepositoryScope,
    ) -> GitHubReviewerEvidence:
        self.exchange_calls.append((code, code_verifier, scope))
        if isinstance(self.exchange_result, Exception):
            raise self.exchange_result
        return self.exchange_result


@dataclass
class ScriptedDiscovery:
    """Return each scripted outcome once, then repeat the last one."""

    outcomes: list[WorkflowDiscoveryOutcome]
    calls: list[tuple[str, RepositoryScope, str | None]] = field(default_factory=list)

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        revision: str | None,
    ) -> WorkflowDiscoveryOutcome:
        self.calls.append((actor, scope, revision))
        if len(self.outcomes) > 1:
            return self.outcomes.pop(0)
        return self.outcomes[0]


@dataclass
class _AllowScope:
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        return actor == ACTOR and scope == SCOPE


@dataclass
class _SnapshotReader:
    snapshot: RepositoryWorkflowSnapshot

    async def read(
        self,
        *,
        scope: RepositoryScope,
        revision: str | None,
    ) -> RepositoryWorkflowSnapshot:
        assert scope == SCOPE
        assert revision is None
        return self.snapshot
