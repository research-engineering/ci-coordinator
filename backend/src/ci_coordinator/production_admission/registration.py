"""Opaque durable projection minted only after cryptographic admission."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn

from ci_coordinator.config_control import RepositoryScope

_AUTHORITY_ID = re.compile(r"production_admission_[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_KEY_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
_MAX_LIFETIME = timedelta(days=7)
_REGISTRATION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class _ProductionAdmissionScopeBinding:
    scope: RepositoryScope
    admission_subject_digest: str
    config_epoch_id: str
    target_registry_hash: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("production admission scope binding must be exact")
        for name, value in (
            ("subject", self.admission_subject_digest),
            ("config epoch", self.config_epoch_id),
            ("target registry", self.target_registry_hash),
        ):
            if type(value) is not str or _DIGEST.fullmatch(value) is None:
                raise ValueError(f"production admission {name} binding is invalid")


class ProductionAdmissionRegistration:
    """Non-forgeable persistence input derived from a verified receipt."""

    __authority_id: str
    __envelope_canonical_json: bytes
    __expires_at: datetime
    __issued_at: datetime
    __key_id: str
    __public_key_spki_der: bytes
    __scope_bindings: tuple[_ProductionAdmissionScopeBinding, ...]

    __slots__ = (
        "__authority_id",
        "__envelope_canonical_json",
        "__expires_at",
        "__issued_at",
        "__key_id",
        "__public_key_spki_der",
        "__scope_bindings",
    )

    def __init__(
        self,
        token: object,
        *,
        authority_id: str,
        key_id: str,
        public_key_spki_der: bytes,
        envelope_canonical_json: bytes,
        issued_at: datetime,
        expires_at: datetime,
        scope_bindings: tuple[_ProductionAdmissionScopeBinding, ...],
    ) -> None:
        if token is not _REGISTRATION_TOKEN:
            raise TypeError("ProductionAdmissionRegistration cannot be constructed directly")
        if type(authority_id) is not str or _AUTHORITY_ID.fullmatch(authority_id) is None:
            raise ValueError("production admission registration identity is invalid")
        if type(key_id) is not str or _KEY_ID.fullmatch(key_id) is None:
            raise ValueError("production admission registration key id is invalid")
        if (
            type(public_key_spki_der) is not bytes
            or len(public_key_spki_der) != 44
            or not public_key_spki_der.startswith(_ED25519_SPKI_PREFIX)
        ):
            raise ValueError("production admission registration public key is not Ed25519 SPKI")
        if (
            type(envelope_canonical_json) is not bytes
            or not 1 <= len(envelope_canonical_json) <= 262_144
        ):
            raise ValueError("production admission registration envelope is invalid")
        for name, value in (("issuance", issued_at), ("expiry", expires_at)):
            if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"production admission registration {name} is invalid")
        if not timedelta(0) < expires_at - issued_at <= _MAX_LIFETIME:
            raise ValueError("production admission registration lifetime is invalid")
        if type(scope_bindings) is not tuple or any(
            type(binding) is not _ProductionAdmissionScopeBinding for binding in scope_bindings
        ):
            raise TypeError("production admission registration scopes must be exact")
        coordinates = tuple(
            (binding.scope.installation_id, binding.scope.repository_id)
            for binding in scope_bindings
        )
        if not coordinates or tuple(sorted(set(coordinates))) != coordinates:
            raise ValueError("production admission registration scopes must be canonical")
        object.__setattr__(self, "_ProductionAdmissionRegistration__authority_id", authority_id)
        object.__setattr__(self, "_ProductionAdmissionRegistration__key_id", key_id)
        object.__setattr__(
            self,
            "_ProductionAdmissionRegistration__public_key_spki_der",
            public_key_spki_der,
        )
        object.__setattr__(
            self,
            "_ProductionAdmissionRegistration__envelope_canonical_json",
            envelope_canonical_json,
        )
        object.__setattr__(self, "_ProductionAdmissionRegistration__issued_at", issued_at)
        object.__setattr__(self, "_ProductionAdmissionRegistration__expires_at", expires_at)
        object.__setattr__(
            self,
            "_ProductionAdmissionRegistration__scope_bindings",
            scope_bindings,
        )

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("ProductionAdmissionRegistration cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("ProductionAdmissionRegistration is immutable")

    def __reduce__(self) -> NoReturn:
        raise TypeError("ProductionAdmissionRegistration cannot be serialized")

    @property
    def authority_id(self) -> str:
        return self.__authority_id

    @property
    def key_id(self) -> str:
        return self.__key_id

    @property
    def public_key_spki_der(self) -> bytes:
        return self.__public_key_spki_der

    @property
    def envelope_canonical_json(self) -> bytes:
        return self.__envelope_canonical_json

    @property
    def issued_at(self) -> datetime:
        return self.__issued_at

    @property
    def expires_at(self) -> datetime:
        return self.__expires_at

    @property
    def scope_bindings(self) -> tuple[_ProductionAdmissionScopeBinding, ...]:
        return self.__scope_bindings


def _issue_production_admission_registration(
    *,
    authority_id: str,
    key_id: str,
    public_key_spki_der: bytes,
    envelope_canonical_json: bytes,
    issued_at: datetime,
    expires_at: datetime,
    scope_bindings: tuple[tuple[RepositoryScope, str, str, str], ...],
) -> ProductionAdmissionRegistration:
    return ProductionAdmissionRegistration(
        _REGISTRATION_TOKEN,
        authority_id=authority_id,
        key_id=key_id,
        public_key_spki_der=public_key_spki_der,
        envelope_canonical_json=envelope_canonical_json,
        issued_at=issued_at,
        expires_at=expires_at,
        scope_bindings=tuple(
            _ProductionAdmissionScopeBinding(*binding) for binding in scope_bindings
        ),
    )
