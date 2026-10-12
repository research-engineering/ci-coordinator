from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import FixedClock
from ci_coordinator.workflow_discovery.component_passport import (
    ComponentLocationOutcome,
    ComponentPassport,
    ComponentReadFailure,
    ComponentReference,
    ComponentRepositoryLocation,
    ComponentRepositoryScope,
    ComponentRevisionObserved,
    ComponentRevisionOutcome,
    ComponentWorkflowBlob,
    declare_components,
)
from ci_coordinator.workflow_discovery.component_resolution import (
    COMPONENT_RESOLUTION_MAXIMUM_TARGETS,
    resolve_component_references,
)
from ci_coordinator.workflow_discovery.outcomes import WorkflowDiscoveryCompleted
from ci_coordinator.workflow_discovery.report import DiscoveryReport
from ci_coordinator.workflow_discovery.service import WorkflowDiscoveryService
from ci_coordinator.workflow_discovery.source import (
    RepositoryIdentity,
    RepositoryWorkflowSnapshot,
    WorkflowSource,
    git_blob_sha1,
)

SCOPE = RepositoryScope(7, 11)
SHARED_SCOPE = RepositoryScope(7, 12)
PRIVATE_SCOPE = RepositoryScope(7, 13)
REVISION = "a" * 40
PINNED = "b" * 40
OBSERVED_AT = datetime(2026, 10, 11, 12, tzinfo=UTC)
CALLER = ".github/workflows/ci.yml"
_STEP_DOCUMENT = (
    "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - uses: {uses}\n"
)
_JOB_DOCUMENT = "name: CI\non: push\njobs:\n  call00:\n    uses: {uses}\n"


@dataclass
class _Authorizer:
    denied: frozenset[RepositoryScope] = frozenset()
    calls: list[RepositoryScope] = field(default_factory=list)

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.calls.append(scope)
        return scope not in self.denied


@dataclass
class _Reader:
    snapshot: RepositoryWorkflowSnapshot

    async def read(
        self,
        *,
        scope: RepositoryScope,
        revision: str | None,
    ) -> RepositoryWorkflowSnapshot:
        return self.snapshot


@dataclass
class _Resolver:
    location: ComponentLocationOutcome = field(
        default_factory=lambda: ComponentRepositoryLocation(
            (
                ComponentRepositoryScope("shared", SHARED_SCOPE),
                ComponentRepositoryScope("private", PRIVATE_SCOPE),
            ),
            True,
        )
    )
    commit: str = "c" * 40
    blobs: dict[str, str | None] = field(default_factory=lambda: {"build.yml": "d" * 40})
    located: list[tuple[str, ...]] = field(default_factory=list)
    reads: list[tuple[RepositoryScope, str, str, tuple[str, ...]]] = field(default_factory=list)
    entered: asyncio.Event | None = None
    release: asyncio.Event | None = None

    async def locate_repositories(
        self,
        *,
        installation_id: int,
        owner: str,
        names: tuple[str, ...],
    ) -> ComponentLocationOutcome:
        assert (installation_id, owner) == (SCOPE.installation_id, "example")
        self.located.append(names)
        if self.entered is not None and self.release is not None:
            self.entered.set()
            await self.release.wait()
        return self.location

    async def read_revision(
        self,
        *,
        scope: RepositoryScope,
        repository: str,
        ref: str,
        workflow_files: tuple[str, ...],
    ) -> ComponentRevisionOutcome:
        self.reads.append((scope, repository, ref, workflow_files))
        return ComponentRevisionObserved(
            self.commit,
            tuple(ComponentWorkflowBlob(name, self.blobs.get(name)) for name in workflow_files),
        )


@dataclass
class _FailingSiblingResolver(_Resolver):
    """Fails one read while its sibling read is suspended inside the provider."""

    fail: bool = True
    sibling_entered: asyncio.Event = field(default_factory=asyncio.Event)
    sibling_cancelled: bool = False

    async def read_revision(
        self,
        *,
        scope: RepositoryScope,
        repository: str,
        ref: str,
        workflow_files: tuple[str, ...],
    ) -> ComponentRevisionOutcome:
        if self.fail and repository == "example/private":
            await self.sibling_entered.wait()
            raise RuntimeError("provider transport failed")
        if self.fail:
            self.sibling_entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.sibling_cancelled = True
                raise
        return await super().read_revision(
            scope=scope,
            repository=repository,
            ref=ref,
            workflow_files=workflow_files,
        )


def test_declarations_distinguish_job_calls_actions_and_images() -> None:
    report = _report(
        f"""\
name: CI
on: push
jobs:
  local:
    uses: ./.github/workflows/reusable.yml
  remote:
    uses: example/shared/.github/workflows/build.yml@{PINNED}
  steps:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: example/shared/actions/setup@{PINNED}
      - uses: ./.github/actions/local
      - uses: docker://ghcr.io/example/tool@sha256:{"e" * 64}
      - uses: docker://alpine:3.20
      - uses: ${{{{ matrix.action }}}}
      - uses: not-an-action
      - run: echo done
""",
        _source(
            "name: Reusable\non: workflow_call\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: true\n",
            ".github/workflows/reusable.yml",
        ),
    )

    by_uses = {item.uses: item for item in declare_components(report)}

    local = by_uses["./.github/workflows/reusable.yml"]
    assert (local.kind, local.resolution, local.immutable, local.resolved_revision) == (
        "reusable_workflow",
        "observed",
        True,
        REVISION,
    )
    assert local.resolved_blob_sha == next(
        source.blob_sha for source in report.sources if source.path == local.path
    )
    assert local.resolved_at is None
    remote = by_uses[f"example/shared/.github/workflows/build.yml@{PINNED}"]
    assert (remote.repository, remote.path, remote.ref, remote.immutable) == (
        "example/shared",
        ".github/workflows/build.yml",
        PINNED,
        True,
    )
    assert remote.unresolved_reason == "resolution_not_requested"
    checkout = by_uses["actions/checkout@v4"]
    assert (checkout.kind, checkout.repository, checkout.path, checkout.immutable) == (
        "action",
        "actions/checkout",
        None,
        False,
    )
    setup = by_uses[f"example/shared/actions/setup@{PINNED}"]
    assert (setup.path, setup.immutable, setup.step_index) == ("actions/setup", True, 1)
    assert by_uses["./.github/actions/local"].unresolved_reason == "workspace_checkout_dependent"
    digest_image = by_uses[f"docker://ghcr.io/example/tool@sha256:{'e' * 64}"]
    assert (digest_image.kind, digest_image.repository, digest_image.immutable) == (
        "docker_image",
        "ghcr.io/example/tool",
        True,
    )
    tagged_image = by_uses["docker://alpine:3.20"]
    assert (tagged_image.ref, tagged_image.digest, tagged_image.immutable) == (
        "3.20",
        None,
        False,
    )
    assert tagged_image.unresolved_reason == "registry_not_read"
    assert by_uses["${{ matrix.action }}"].unresolved_reason == "dynamic_reference"
    assert by_uses["not-an-action"].unresolved_reason == "invalid_reference"
    assert len(by_uses) == 9


@pytest.mark.parametrize(
    "job",
    ["    steps: run-this\n", "    uses: [example/shared/.github/workflows/build.yml@main]\n"],
    ids=["unprojected-steps", "non-scalar-job-call"],
)
def test_unprojected_uses_make_declarations_incomplete(job: str) -> None:
    report = _report(f"name: CI\non: push\njobs:\n  test:\n{job}")

    assert report.complete is True
    assert declare_components(report) == ()
    assert ComponentPassport.create(report, ()).declarations_complete is False
    projected = _report(
        "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: true\n"
    )
    assert ComponentPassport.create(projected, ()).declarations_complete is True


@pytest.mark.parametrize("resolve", [False, True], ids=["unresolved", "resolved"])
@pytest.mark.parametrize(
    ("document", "uses", "kind"),
    [
        (
            _STEP_DOCUMENT.format(uses='"docker://alpine: 3.20"'),
            "docker://alpine: 3.20",
            "docker_image",
        ),
        (_STEP_DOCUMENT.format(uses='"./ local"'), "./ local", "action"),
        (
            _STEP_DOCUMENT.format(uses='"example/shared/ setup@v1"'),
            "example/shared/ setup@v1",
            "action",
        ),
        (
            _STEP_DOCUMENT.format(uses='"example/shared/actions/setup@\\u00a0v1"'),
            "example/shared/actions/setup@\u00a0v1",
            "action",
        ),
        (
            _JOB_DOCUMENT.format(uses="example/shared/.github/workflows/bad\\name.yml@main"),
            "example/shared/.github/workflows/bad\\name.yml@main",
            "reusable_workflow",
        ),
        (
            _JOB_DOCUMENT.format(uses=f"example/shared/.github/workflows/{'x' * 600}.yml@main"),
            f"example/shared/.github/workflows/{'x' * 600}.yml@main",
            "reusable_workflow",
        ),
    ],
    ids=[
        "docker-tag-whitespace",
        "local-action-whitespace",
        "action-path-whitespace",
        "ref-unicode-whitespace",
        "callee-backslash",
        "callee-overlong",
    ],
)
def test_inadmissible_extracted_coordinates_are_invalid_without_provider_reads(
    document: str,
    uses: str,
    kind: str,
    resolve: bool,
) -> None:
    resolver = _Resolver() if resolve else None
    outcome = _discover(_source(document), resolver)

    (reference,) = outcome.component_passport.references
    assert (reference.uses, reference.kind, reference.repository) == (uses, kind, None)
    assert (reference.resolution, reference.unresolved_reason) == (
        "unresolved",
        "invalid_reference",
    )
    if resolver is not None:
        assert (resolver.located, resolver.reads) == ([], [])


def test_failed_read_cancels_and_joins_sibling_reads_before_admission_release() -> None:
    async def scenario() -> WorkflowDiscoveryCompleted:
        resolver = _FailingSiblingResolver()
        service = _service(
            _caller(
                "example/private/.github/workflows/build.yml@main",
                "example/shared/.github/workflows/build.yml@main",
            ),
            resolver,
        )
        with pytest.raises(ExceptionGroup) as raised:
            await service(actor="operator", scope=SCOPE, revision=None)
        assert raised.group_contains(RuntimeError, match="provider transport failed")
        # The sibling was cancelled and joined before the admission lease was released.
        assert resolver.sibling_cancelled
        resolver.fail = False
        following = await service(actor="operator", scope=SCOPE, revision=None)
        assert isinstance(following, WorkflowDiscoveryCompleted)
        return following

    following = asyncio.run(scenario())

    assert [item.resolution for item in following.component_passport.references] == [
        "observed",
        "observed",
    ]


def test_other_accounts_are_never_located_or_read() -> None:
    resolver = _Resolver()
    outcome = _discover(_caller("other-org/shared/.github/workflows/build.yml@main"), resolver)

    (reference,) = outcome.component_passport.references
    assert (reference.resolution, reference.unresolved_reason) == (
        "unresolved",
        "outside_caller_installation",
    )
    assert resolver.located == []
    assert resolver.reads == []


def test_callee_authorization_precedes_every_callee_read() -> None:
    resolver = _Resolver()
    authorizer = _Authorizer(denied=frozenset({PRIVATE_SCOPE}))
    outcome = _discover(
        _caller(
            "Example/Private/.github/workflows/build.yml@main",
            "example/shared/.github/workflows/build.yml@main",
        ),
        resolver,
        authorizer=authorizer,
    )

    by_repository = {item.repository: item for item in outcome.component_passport.references}
    denied = by_repository["Example/Private"]
    assert (denied.resolution, denied.resolved_revision) == ("denied", None)
    assert [read[0] for read in resolver.reads] == [SHARED_SCOPE]
    assert authorizer.calls == [SCOPE, PRIVATE_SCOPE, SHARED_SCOPE]
    observed = by_repository["example/shared"]
    assert (observed.resolution, observed.resolved_revision, observed.resolved_blob_sha) == (
        "observed",
        "c" * 40,
        "d" * 40,
    )
    assert observed.resolved_at == OBSERVED_AT


def test_observed_action_binds_a_commit_without_a_callee_blob() -> None:
    resolver = _Resolver()
    outcome = _discover(
        _source(
            "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: example/shared/actions/setup@v2\n"
        ),
        resolver,
    )

    (reference,) = outcome.component_passport.references
    assert (reference.resolution, reference.resolved_revision, reference.resolved_blob_sha) == (
        "observed",
        "c" * 40,
        None,
    )
    assert resolver.reads == [(SHARED_SCOPE, "example/shared", "v2", ())]


def test_missing_callee_workflow_is_not_an_observed_callee() -> None:
    resolver = _Resolver(blobs={})
    outcome = _discover(_caller("example/shared/.github/workflows/build.yml@main"), resolver)

    (reference,) = outcome.component_passport.references
    assert (reference.resolution, reference.unresolved_reason) == (
        "unresolved",
        "callee_workflow_missing",
    )


@pytest.mark.parametrize(
    ("complete", "reason"),
    [(True, "not_in_caller_installation"), (False, "installation_inventory_limit_exceeded")],
)
def test_absent_repository_reason_depends_on_inventory_completeness(
    complete: bool,
    reason: str,
) -> None:
    resolver = _Resolver(location=ComponentRepositoryLocation((), complete))
    outcome = _discover(_caller("example/absent/.github/workflows/build.yml@main"), resolver)

    (reference,) = outcome.component_passport.references
    assert reference.unresolved_reason == reason
    assert resolver.reads == []


def test_inventory_failure_leaves_every_target_unresolved() -> None:
    resolver = _Resolver(location=ComponentReadFailure("installation_inventory_unavailable"))
    outcome = _discover(_caller("example/shared/.github/workflows/build.yml@main"), resolver)

    (reference,) = outcome.component_passport.references
    assert reference.unresolved_reason == "installation_inventory_unavailable"


def test_moving_tag_changes_only_the_passport_not_caller_evidence() -> None:
    source = _caller("example/shared/.github/workflows/build.yml@v1")
    before = _discover(source, _Resolver(commit="c" * 40))
    after = _discover(source, _Resolver(commit="f" * 40))

    assert before.report == after.report
    assert before.proposal == after.proposal
    assert [item.resolved_revision for item in before.component_passport.references] == ["c" * 40]
    assert [item.resolved_revision for item in after.component_passport.references] == ["f" * 40]


def test_targets_beyond_the_read_bound_remain_unresolved() -> None:
    count = COMPONENT_RESOLUTION_MAXIMUM_TARGETS + 1
    names = [f"repo{index:02d}" for index in range(count)]
    resolver = _Resolver(
        location=ComponentRepositoryLocation(
            tuple(
                ComponentRepositoryScope(name, RepositoryScope(7, 100 + index))
                for index, name in enumerate(names)
            ),
            True,
        )
    )
    outcome = _discover(
        _caller(*(f"example/{name}/.github/workflows/build.yml@main" for name in names)),
        resolver,
    )

    reasons = {
        item.repository: item.unresolved_reason for item in outcome.component_passport.references
    }
    assert len(resolver.reads) == COMPONENT_RESOLUTION_MAXIMUM_TARGETS
    assert reasons[f"example/{names[-1]}"] == "resolution_limit_exceeded"
    assert all(reasons[f"example/{name}"] is None for name in names[:-1])


def test_resolution_deadline_leaves_pending_targets_unresolved() -> None:
    report = _report(_caller_document("example/shared/.github/workflows/build.yml@main"))
    release = asyncio.Event()
    resolver = _Resolver(entered=asyncio.Event(), release=release)

    references = asyncio.run(
        resolve_component_references(
            report,
            actor="operator",
            authorizer=_Authorizer(),
            resolver=resolver,
            clock=FixedClock(OBSERVED_AT),
            deadline_seconds=0.01,
        )
    )

    assert [item.unresolved_reason for item in references] == ["resolution_deadline_exceeded"]
    assert resolver.reads == []


def test_overlapping_resolution_is_refused_without_provider_reads() -> None:
    async def scenario() -> tuple[WorkflowDiscoveryCompleted, WorkflowDiscoveryCompleted]:
        resolver = _Resolver(entered=asyncio.Event(), release=asyncio.Event())
        service = _service(_caller("example/shared/.github/workflows/build.yml@main"), resolver)
        first = asyncio.create_task(service(actor="operator", scope=SCOPE, revision=None))
        assert resolver.entered is not None and resolver.release is not None
        await asyncio.wait_for(resolver.entered.wait(), timeout=1)
        second = await service(actor="operator", scope=SCOPE, revision=None)
        resolver.release.set()
        completed = await first
        assert isinstance(completed, WorkflowDiscoveryCompleted)
        assert isinstance(second, WorkflowDiscoveryCompleted)
        assert len(resolver.located) == 1
        return completed, second

    first, second = asyncio.run(scenario())

    assert [item.resolution for item in first.component_passport.references] == ["observed"]
    assert [item.unresolved_reason for item in second.component_passport.references] == [
        "resolution_overloaded"
    ]


def test_service_without_resolver_never_claims_resolution() -> None:
    outcome = _discover(_caller("example/shared/.github/workflows/build.yml@main"), None)

    (reference,) = outcome.component_passport.references
    assert reference.unresolved_reason == "resolution_not_requested"
    with pytest.raises(ValueError, match="exactly one resolver and clock"):
        WorkflowDiscoveryService(
            authorizer=_Authorizer(),
            reader=_Reader(_snapshot(_caller("example/x/.github/workflows/y.yml@main"))),
            component_resolver=_Resolver(),
        )


def test_completed_discovery_refuses_a_passport_that_omits_a_declaration() -> None:
    outcome = _discover(_caller("example/shared/.github/workflows/build.yml@main"), None)

    with pytest.raises(ValueError, match="component passport must totally bind"):
        replace(
            outcome,
            component_passport=replace(outcome.component_passport, references=()),
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda item: replace(item, resolution="denied"),
        lambda item: replace(item, resolved_at=None),
        lambda item: replace(item, resolved_blob_sha=None),
        lambda item: replace(item, unresolved_reason="ref_not_found"),
    ],
    ids=["denied-with-revision", "no-resolved-at", "no-callee-blob", "observed-with-reason"],
)
def test_reference_resolution_shape_is_closed(
    change: Callable[[ComponentReference], ComponentReference],
) -> None:
    outcome = _discover(_caller("example/shared/.github/workflows/build.yml@main"), _Resolver())
    (observed,) = outcome.component_passport.references
    assert observed.resolution == "observed"

    with pytest.raises(ValueError):
        change(observed)


def test_denied_reference_cannot_name_a_same_commit_component() -> None:
    report = _report(_caller_document("./.github/workflows/ci.yml"))
    (local,) = declare_components(report)

    with pytest.raises(ValueError, match="provider-resolvable"):
        replace(
            local,
            resolution="denied",
            resolved_revision=None,
            resolved_blob_sha=None,
        )


def _discover(
    source: WorkflowSource,
    resolver: _Resolver | None,
    *,
    authorizer: _Authorizer | None = None,
) -> WorkflowDiscoveryCompleted:
    result = asyncio.run(
        _service(source, resolver, authorizer=authorizer)(
            actor="operator",
            scope=SCOPE,
            revision=None,
        )
    )
    assert isinstance(result, WorkflowDiscoveryCompleted)
    return result


def _service(
    source: WorkflowSource,
    resolver: _Resolver | None,
    *,
    authorizer: _Authorizer | None = None,
) -> WorkflowDiscoveryService:
    return WorkflowDiscoveryService(
        authorizer=authorizer or _Authorizer(),
        reader=_Reader(_snapshot(source)),
        component_resolver=resolver,
        clock=None if resolver is None else FixedClock(OBSERVED_AT),
    )


def _report(document: str, *extra: WorkflowSource) -> DiscoveryReport:
    return _discover_report(_snapshot(_source(document), *extra))


def _discover_report(snapshot: RepositoryWorkflowSnapshot) -> DiscoveryReport:
    result = asyncio.run(
        WorkflowDiscoveryService(authorizer=_Authorizer(), reader=_Reader(snapshot))(
            actor="operator",
            scope=SCOPE,
            revision=None,
        )
    )
    assert isinstance(result, WorkflowDiscoveryCompleted)
    return result.report


def _caller(*coordinates: str) -> WorkflowSource:
    return _source(_caller_document(*coordinates))


def _caller_document(*coordinates: str) -> str:
    jobs = "".join(
        f"  call{index:02d}:\n    uses: {coordinate}\n"
        for index, coordinate in enumerate(coordinates)
    )
    return f"name: CI\non: push\njobs:\n{jobs}"


def _snapshot(*sources: WorkflowSource) -> RepositoryWorkflowSnapshot:
    return RepositoryWorkflowSnapshot.create(
        repository=RepositoryIdentity(SCOPE, "example", "repo", "master"),
        revision=REVISION,
        sources=sources,
    )


def _source(document: str, path: str = CALLER) -> WorkflowSource:
    content = document.encode()
    return WorkflowSource(path, git_blob_sha1(content), len(content), content)
