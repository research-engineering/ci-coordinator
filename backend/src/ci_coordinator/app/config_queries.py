"""Authorized bounded reads for repository configuration lifecycle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from ci_coordinator.app.config_admission import ConfigScopeAuthorizer
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import (
    MAX_CONFIG_EPOCH_PAGE_SIZE,
    ConfigEpochStatus,
    ConfigEpochStoreUnavailable,
)


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


def _require_scope(scope: object) -> None:
    if type(scope) is not RepositoryScope:
        raise TypeError("config query requires an exact repository scope")


def _require_epoch_id(epoch_id: object) -> None:
    if type(epoch_id) is not str or re.fullmatch(r"[0-9a-f]{64}", epoch_id) is None:
        raise ValueError("config epoch cursor is invalid")
