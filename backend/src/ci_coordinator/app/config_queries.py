"""Authorized bounded reads for repository configuration lifecycle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ci_coordinator.app.config_admission import ConfigScopeAuthorizer
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import (
    MAX_CONFIG_EPOCH_PAGE_SIZE,
    ConfigEpochStatus,
    ConfigEpochStoreUnavailable,
    CoverageRelation,
)
from ci_coordinator.planning_core import ConservativeEpochCoverageComparator
from ci_coordinator.proposal_review import (
    MAX_CHANGED_POINTERS,
    MAX_SEMANTIC_DIFF_BYTES,
    PolicySemanticDiff,
    SemanticDiffLimitExceeded,
    compare_policy_drafts,
)

# Preview pointer bounds are the review semantic-diff owner's bounds, projected for HTTP.
MAX_PREVIEW_CHANGED_POINTERS: Final = MAX_CHANGED_POINTERS
MAX_PREVIEW_POINTER_BYTES: Final = MAX_SEMANTIC_DIFF_BYTES


class ConfigEpochQueryStore(Protocol):
    async def read_status(
        self,
        scope: RepositoryScope,
        *,
        after_epoch_id: str | None,
        limit: int,
    ) -> ConfigEpochStatus: ...

    async def load_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ValidatedEpochDraft | None: ...


@dataclass(frozen=True, slots=True)
class ConfigStatusAvailable:
    value: ConfigEpochStatus

    def __post_init__(self) -> None:
        if type(self.value) is not ConfigEpochStatus:
            raise TypeError("config status result requires an exact status value")


@dataclass(frozen=True, slots=True)
class ConfigSourceAvailable:
    value: ValidatedEpochDraft

    def __post_init__(self) -> None:
        if type(self.value) is not ValidatedEpochDraft:
            raise TypeError("config source result requires an exact validated draft")


type ConfigPreviewCoverageRelation = Literal["equal", "greater", "less", "incomparable", "unknown"]


@dataclass(frozen=True, slots=True)
class ConfigEpochPreview:
    """Read-only comparison of two retained same-scope epochs; it decides nothing."""

    scope: RepositoryScope
    base_epoch_id: str
    target_epoch_id: str
    coverage_relation: ConfigPreviewCoverageRelation
    # None means the exact pointer set exceeds the admitted semantic-diff bound.
    diff: PolicySemanticDiff | None

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        _require_epoch_id(self.base_epoch_id)
        _require_epoch_id(self.target_epoch_id)
        if self.coverage_relation not in {"equal", "greater", "less", "incomparable", "unknown"}:
            raise ValueError("config preview coverage relation is not admitted")
        if self.diff is not None and (
            type(self.diff) is not PolicySemanticDiff
            or self.diff.scope != self.scope
            or self.diff.base_epoch_id != self.base_epoch_id
            or self.diff.target_epoch_id != self.target_epoch_id
        ):
            raise ValueError("config preview diff does not bind its scope and epochs")


@dataclass(frozen=True, slots=True)
class ConfigPreviewAvailable:
    value: ConfigEpochPreview

    def __post_init__(self) -> None:
        if type(self.value) is not ConfigEpochPreview:
            raise TypeError("config preview result requires an exact preview value")


@dataclass(frozen=True, slots=True)
class ConfigQueryForbidden:
    pass


@dataclass(frozen=True, slots=True)
class ConfigQueryNotFound:
    pass


@dataclass(frozen=True, slots=True)
class ConfigQueryUnavailable:
    pass


type ConfigStatusResult = ConfigStatusAvailable | ConfigQueryForbidden | ConfigQueryUnavailable
type ConfigSourceResult = (
    ConfigSourceAvailable | ConfigQueryForbidden | ConfigQueryNotFound | ConfigQueryUnavailable
)
type ConfigPreviewResult = (
    ConfigPreviewAvailable | ConfigQueryForbidden | ConfigQueryNotFound | ConfigQueryUnavailable
)


class ConfigQueryUseCase(Protocol):
    async def status(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_epoch_id: str | None,
        limit: int,
    ) -> ConfigStatusResult: ...

    async def source(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ConfigSourceResult: ...

    async def preview(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        base_epoch_id: str,
        target_epoch_id: str,
    ) -> ConfigPreviewResult: ...


class ConfigQueryService:
    def __init__(
        self,
        *,
        authorizer: ConfigScopeAuthorizer,
        store: ConfigEpochQueryStore,
    ) -> None:
        self._authorizer = authorizer
        self._store = store

    async def status(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_epoch_id: str | None,
        limit: int,
    ) -> ConfigStatusResult:
        _require_scope(scope)
        if after_epoch_id is not None:
            _require_epoch_id(after_epoch_id)
        if type(limit) is not int or not 1 <= limit <= MAX_CONFIG_EPOCH_PAGE_SIZE:
            raise ValueError("config epoch page size is outside its admitted bound")
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return ConfigQueryForbidden()
        try:
            value = await self._store.read_status(
                scope,
                after_epoch_id=after_epoch_id,
                limit=limit,
            )
        except ConfigEpochStoreUnavailable:
            return ConfigQueryUnavailable()
        if value.scope != scope:
            return ConfigQueryUnavailable()
        return ConfigStatusAvailable(value)

    async def source(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ConfigSourceResult:
        _require_scope(scope)
        _require_epoch_id(epoch_id)
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return ConfigQueryForbidden()
        try:
            value = await self._store.load_epoch(scope, epoch_id)
        except ConfigEpochStoreUnavailable:
            return ConfigQueryUnavailable()
        if value is None:
            return ConfigQueryNotFound()
        if value.scope != scope or value.epoch_id != epoch_id:
            return ConfigQueryUnavailable()
        return ConfigSourceAvailable(value)

    async def preview(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        base_epoch_id: str,
        target_epoch_id: str,
    ) -> ConfigPreviewResult:
        """Compare two retained epochs with the rollback coverage and review diff owners."""
        _require_scope(scope)
        _require_epoch_id(base_epoch_id)
        _require_epoch_id(target_epoch_id)
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return ConfigQueryForbidden()
        try:
            base = await self._store.load_epoch(scope, base_epoch_id)
            target = await self._store.load_epoch(scope, target_epoch_id)
        except ConfigEpochStoreUnavailable:
            return ConfigQueryUnavailable()
        if base is None or target is None:
            return ConfigQueryNotFound()
        if (
            base.scope != scope
            or target.scope != scope
            or base.epoch_id != base_epoch_id
            or target.epoch_id != target_epoch_id
        ):
            return ConfigQueryUnavailable()
        relation: CoverageRelation = await ConservativeEpochCoverageComparator(
            base,
            target,
        ).compare(scope=scope, active_epoch_id=base_epoch_id, target_epoch_id=target_epoch_id)
        if relation == "unavailable":
            return ConfigQueryUnavailable()
        try:
            diff: PolicySemanticDiff | None = compare_policy_drafts(base, target)
        except SemanticDiffLimitExceeded:
            diff = None
        return ConfigPreviewAvailable(
            ConfigEpochPreview(
                scope=scope,
                base_epoch_id=base_epoch_id,
                target_epoch_id=target_epoch_id,
                coverage_relation=relation,
                diff=diff,
            )
        )


def _require_scope(scope: object) -> None:
    if type(scope) is not RepositoryScope:
        raise TypeError("config query requires an exact repository scope")


def _require_epoch_id(epoch_id: object) -> None:
    if type(epoch_id) is not str or re.fullmatch(r"[0-9a-f]{64}", epoch_id) is None:
        raise ValueError("config epoch cursor is invalid")
