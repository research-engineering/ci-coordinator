"""Bounded resolution of remote component refs inside the caller's granted installation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import Clock, utf16_sort_key
from ci_coordinator.workflow_discovery.component_passport import (
    ComponentReadFailure,
    ComponentReference,
    ComponentRevisionObserved,
    ComponentUnresolvedReason,
    declare_components,
)
from ci_coordinator.workflow_discovery.ports import (
    ComponentRevisionResolver,
    WorkflowDiscoveryAuthorizer,
)
from ci_coordinator.workflow_discovery.report import DiscoveryReport

COMPONENT_RESOLUTION_MAXIMUM_TARGETS: Final = 8
COMPONENT_RESOLUTION_MAXIMUM_CONCURRENCY: Final = 4
COMPONENT_RESOLUTION_DEADLINE_SECONDS: Final = 10.0


@dataclass(frozen=True, slots=True)
class _Target:
    owner: str
    name: str
    ref: str


@dataclass(frozen=True, slots=True)
class _Observed:
    revision: ComponentRevisionObserved
    resolved_at: datetime


@dataclass(frozen=True, slots=True)
class _Denied:
    pass


@dataclass(frozen=True, slots=True)
class _Unresolved:
    reason: ComponentUnresolvedReason


type _TargetResult = _Observed | _Denied | _Unresolved


def unresolved_component_references(
    report: DiscoveryReport,
    reason: ComponentUnresolvedReason,
) -> tuple[ComponentReference, ...]:
    """Project declarations whose provider read was not performed for `reason`."""

    return tuple(
        replace(item, unresolved_reason=reason) if item.provider_resolvable else item
        for item in declare_components(report)
    )


async def resolve_component_references(
    report: DiscoveryReport,
    *,
    actor: str,
    authorizer: WorkflowDiscoveryAuthorizer,
    resolver: ComponentRevisionResolver,
    clock: Clock,
    deadline_seconds: float = COMPONENT_RESOLUTION_DEADLINE_SECONDS,
) -> tuple[ComponentReference, ...]:
    """Resolve remote refs only in the caller installation and only for authorized scopes.

    A component owned by another account cannot be in the caller installation,
    so it is never read. At most eight distinct repository/ref targets are read;
    later targets in canonical order remain explicitly unresolved.
    """

    declarations = declare_components(report)
    caller_owner = report.repository.owner.lower()
    workflow_files: dict[_Target, set[str]] = {}
    results: dict[_Target, _TargetResult] = {}
    for item in declarations:
        target = _target(item)
        if target is None:
            continue
        if target.owner != caller_owner:
            results[target] = _Unresolved("outside_caller_installation")
            continue
        files = workflow_files.setdefault(target, set())
        if item.kind == "reusable_workflow" and item.path is not None:
            files.add(item.path.rsplit("/", 1)[1])
    ordered = sorted(
        workflow_files,
        key=lambda item: tuple(
            utf16_sort_key(value) for value in (item.owner, item.name, item.ref)
        ),
    )
    admitted = ordered[:COMPONENT_RESOLUTION_MAXIMUM_TARGETS]
    for target in ordered[COMPONENT_RESOLUTION_MAXIMUM_TARGETS:]:
        results[target] = _Unresolved("resolution_limit_exceeded")
    if admitted:
        deadline = asyncio.timeout(deadline_seconds)
        try:
            async with deadline:
                await _resolve_admitted(
                    report,
                    admitted,
                    workflow_files,
                    results,
                    actor=actor,
                    authorizer=authorizer,
                    resolver=resolver,
                    clock=clock,
                )
        except TimeoutError:
            if not deadline.expired():
                raise
        for target in admitted:
            results.setdefault(target, _Unresolved("resolution_deadline_exceeded"))
    return tuple(_apply(item, results) for item in declarations)


async def _resolve_admitted(
    report: DiscoveryReport,
    admitted: list[_Target],
    workflow_files: dict[_Target, set[str]],
    results: dict[_Target, _TargetResult],
    *,
    actor: str,
    authorizer: WorkflowDiscoveryAuthorizer,
    resolver: ComponentRevisionResolver,
    clock: Clock,
) -> None:
    location = await resolver.locate_repositories(
        installation_id=report.repository.scope.installation_id,
        owner=report.repository.owner,
        names=tuple(sorted({target.name for target in admitted}, key=utf16_sort_key)),
    )
    if isinstance(location, ComponentReadFailure):
        for target in admitted:
            results[target] = _Unresolved(location.reason)
        return
    scopes = {item.name.lower(): item.scope for item in location.repositories}
    authorized: dict[RepositoryScope, bool] = {}
    readable: list[tuple[_Target, RepositoryScope]] = []
    for target in admitted:
        scope = scopes.get(target.name)
        if scope is None:
            results[target] = _Unresolved(
                "not_in_caller_installation"
                if location.complete
                else "installation_inventory_limit_exceeded"
            )
            continue
        if scope not in authorized:
            # Authorization precedes every callee read, exactly as for the caller scope.
            authorized[scope] = await authorizer.allows_scope(actor=actor, scope=scope)
        if authorized[scope]:
            readable.append((target, scope))
        else:
            results[target] = _Denied()
    semaphore = asyncio.Semaphore(COMPONENT_RESOLUTION_MAXIMUM_CONCURRENCY)

    async def read(target: _Target, scope: RepositoryScope) -> None:
        async with semaphore:
            outcome = await resolver.read_revision(
                scope=scope,
                repository=f"{target.owner}/{target.name}",
                ref=target.ref,
                workflow_files=tuple(sorted(workflow_files[target], key=utf16_sort_key)),
            )
        if isinstance(outcome, ComponentReadFailure):
            results[target] = _Unresolved(outcome.reason)
        elif isinstance(outcome, ComponentRevisionObserved):
            results[target] = _Observed(outcome, clock.now())
        else:
            raise TypeError("component revision resolver returned an unsupported outcome")

    # The task group cancels and joins every sibling read before a failure or
    # deadline leaves this function, so no provider read outlives the caller's
    # resolution admission lease.
    async with asyncio.TaskGroup() as reads:
        for target, scope in readable:
            reads.create_task(read(target, scope))


def _target(item: ComponentReference) -> _Target | None:
    if not item.provider_resolvable or item.repository is None or item.ref is None:
        return None
    owner, name = item.repository.split("/")
    return _Target(owner.lower(), name.lower(), item.ref)


def _apply(item: ComponentReference, results: dict[_Target, _TargetResult]) -> ComponentReference:
    target = _target(item)
    if target is None:
        return item
    result = results[target]
    if isinstance(result, _Denied):
        return replace(item, resolution="denied", unresolved_reason=None)
    if isinstance(result, _Unresolved):
        return replace(item, unresolved_reason=result.reason)
    blob: str | None = None
    if item.kind == "reusable_workflow" and item.path is not None:
        file_name = item.path.rsplit("/", 1)[1]
        blob = next(
            (
                entry.blob_sha
                for entry in result.revision.workflow_blobs
                if entry.file_name == file_name
            ),
            None,
        )
        if blob is None:
            return replace(item, unresolved_reason="callee_workflow_missing")
    return replace(
        item,
        resolution="observed",
        unresolved_reason=None,
        resolved_revision=result.revision.commit_sha,
        resolved_blob_sha=blob,
        resolved_at=result.resolved_at,
    )
