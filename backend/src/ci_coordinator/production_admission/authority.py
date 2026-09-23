"""Opaque, time-bounded capabilities derived only by receipt verification."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import Clock
from ci_coordinator.production_admission.current_evidence import CurrentProductionEvidence
from ci_coordinator.production_admission.model import (
    ProductionAdmissionReceipt,
    ProductionCandidateSubject,
    ProductionPlanSubject,
    ProductionScopeGrant,
)
from ci_coordinator.production_admission.registration import (
    ProductionAdmissionRegistration,
)

_GRANT_TOKEN = object()
_AUTHORIZATION_TOKEN = object()
_ISSUANCE_GUARD_TOKEN = object()
_MAX_CLOCK_SKEW = timedelta(minutes=5)


class ProductionIssuanceGuard:
    __admission_subject_digest: str
    __authority_id: str
    __not_after: datetime
    __subject: ProductionPlanSubject
    __current_evidence: CurrentProductionEvidence | None

    __slots__ = (
        "__admission_subject_digest",
        "__authority_id",
        "__current_evidence",
        "__not_after",
        "__subject",
    )

    def __init__(
        self,
        token: object,
        *,
        admission_subject_digest: str,
        authority_id: str,
        not_after: datetime,
        subject: ProductionPlanSubject,
        current_evidence: CurrentProductionEvidence | None = None,
    ) -> None:
        if token is not _ISSUANCE_GUARD_TOKEN:
            raise TypeError("ProductionIssuanceGuard cannot be constructed directly")
        if re.fullmatch(r"production_admission_[0-9a-f]{32}", authority_id) is None:
            raise ValueError("production issuance authority id is invalid")
        if (
            type(not_after) is not datetime
            or not_after.tzinfo is None
            or not_after.utcoffset() is None
        ):
            raise ValueError("production issuance authority expiry must be timezone-aware")
        if (
            type(admission_subject_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", admission_subject_digest) is None
        ):
            raise ValueError("production issuance admission subject is invalid")
        if type(subject) is not ProductionPlanSubject:
            raise TypeError("production issuance guard subject must be exact")
        if current_evidence is not None and (
            type(current_evidence) is not CurrentProductionEvidence
            or current_evidence.candidate != subject.candidate
            or current_evidence.scope_grant.admission_subject_digest != admission_subject_digest
        ):
            raise ValueError("current production evidence differs from the issuance subject")
        object.__setattr__(
            self, "_ProductionIssuanceGuard__admission_subject_digest", admission_subject_digest
        )
        object.__setattr__(self, "_ProductionIssuanceGuard__authority_id", authority_id)
        object.__setattr__(self, "_ProductionIssuanceGuard__not_after", not_after)
        object.__setattr__(self, "_ProductionIssuanceGuard__subject", subject)
        object.__setattr__(self, "_ProductionIssuanceGuard__current_evidence", current_evidence)

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("ProductionIssuanceGuard is immutable")

    @property
    def admission_subject_digest(self) -> str:
        return self.__admission_subject_digest

    @property
    def authority_id(self) -> str:
        return self.__authority_id

    @property
    def current_evidence(self) -> CurrentProductionEvidence | None:
        return self.__current_evidence

    @property
    def not_after(self) -> datetime:
        return self.__not_after

    @property
    def scope(self) -> RepositoryScope:
        return self.__subject.candidate.scope

    @property
    def config_epoch_id(self) -> str:
        return self.__subject.candidate.config_epoch_id

    @property
    def execution_plan_id(self) -> str:
        return self.__subject.candidate.execution_plan_id

    @property
    def catalog_hash(self) -> str:
        return self.__subject.candidate.catalog_hash

    @property
    def target_registry_hash(self) -> str:
        return self.__subject.target_registry_hash

    @property
    def reconciliation_subject_id(self) -> str:
        return self.__subject.reconciliation_subject_id

    @property
    def workflow_ref(self) -> str | None:
        return self.__subject.candidate.workflow_ref

    @property
    def job_workflow_ref(self) -> str | None:
        return self.__subject.candidate.job_workflow_ref


class AuthorizedProductionAdmission:
    """One non-forgeable authorization for one exact selected-plan subject."""

    __authority_id: str
    __not_after: datetime
    __subject: ProductionPlanSubject
    __admission_subject_digest: str
    __current_evidence: CurrentProductionEvidence | None

    __slots__ = (
        "__admission_subject_digest",
        "__authority_id",
        "__current_evidence",
        "__not_after",
        "__subject",
    )

    def __init__(
        self,
        token: object,
        *,
        authority_id: str,
        not_after: datetime,
        subject: ProductionPlanSubject,
        admission_subject_digest: str = "",
        current_evidence: CurrentProductionEvidence | None = None,
    ) -> None:
        if token is not _AUTHORIZATION_TOKEN:
            raise TypeError("AuthorizedProductionAdmission cannot be constructed directly")
        object.__setattr__(self, "_AuthorizedProductionAdmission__authority_id", authority_id)
        object.__setattr__(
            self,
            "_AuthorizedProductionAdmission__admission_subject_digest",
            admission_subject_digest,
        )
        object.__setattr__(self, "_AuthorizedProductionAdmission__not_after", not_after)
        object.__setattr__(self, "_AuthorizedProductionAdmission__subject", subject)
        object.__setattr__(
            self, "_AuthorizedProductionAdmission__current_evidence", current_evidence
        )

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("AuthorizedProductionAdmission cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("AuthorizedProductionAdmission is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("AuthorizedProductionAdmission cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("AuthorizedProductionAdmission cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("AuthorizedProductionAdmission cannot be serialized")

    @property
    def authority_id(self) -> str:
        return self.__authority_id

    @property
    def not_after(self) -> datetime:
        return self.__not_after

    def binds(self, subject: ProductionPlanSubject) -> bool:
        return type(subject) is ProductionPlanSubject and subject == self.__subject

    def issuance_guard(self) -> ProductionIssuanceGuard:
        return ProductionIssuanceGuard(
            _ISSUANCE_GUARD_TOKEN,
            admission_subject_digest=self.__admission_subject_digest,
            authority_id=self.__authority_id,
            not_after=self.__not_after,
            subject=self.__subject,
            current_evidence=self.__current_evidence,
        )


class ProductionAdmissionGrant:
    """Verified receipt authority; only this owner can mint plan authorization."""

    __authority_id: str
    __clock: Clock
    __minimum_remaining_seconds: int
    __receipt: ProductionAdmissionReceipt
    __registration: ProductionAdmissionRegistration

    __slots__ = (
        "__authority_id",
        "__clock",
        "__minimum_remaining_seconds",
        "__receipt",
        "__registration",
    )

    def __init__(
        self,
        token: object,
        *,
        authority_id: str,
        receipt: ProductionAdmissionReceipt,
        clock: Clock,
        minimum_remaining_seconds: int,
        registration: ProductionAdmissionRegistration | None = None,
    ) -> None:
        if token is not _GRANT_TOKEN:
            raise TypeError("ProductionAdmissionGrant cannot be constructed directly")
        if type(receipt) is not ProductionAdmissionReceipt:
            raise TypeError("production admission grant requires an exact receipt")
        if type(registration) is not ProductionAdmissionRegistration:
            raise TypeError("production admission grant requires an exact registration")
        if registration.authority_id != authority_id:
            raise ValueError("production admission registration identity does not match")
        if (
            type(minimum_remaining_seconds) is not int
            or not 0 <= minimum_remaining_seconds <= 7_200
        ):
            raise ValueError("production admission remaining lifetime bound is invalid")
        object.__setattr__(self, "_ProductionAdmissionGrant__authority_id", authority_id)
        object.__setattr__(self, "_ProductionAdmissionGrant__receipt", receipt)
        object.__setattr__(self, "_ProductionAdmissionGrant__registration", registration)
        object.__setattr__(self, "_ProductionAdmissionGrant__clock", clock)
        object.__setattr__(
            self,
            "_ProductionAdmissionGrant__minimum_remaining_seconds",
            minimum_remaining_seconds,
        )

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del kwargs
        raise TypeError("ProductionAdmissionGrant cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("ProductionAdmissionGrant is immutable")

    @property
    def authority_id(self) -> str:
        return self.__authority_id

    @property
    def not_after(self) -> datetime:
        return self.__receipt.expires_at

    @property
    def registration(self) -> ProductionAdmissionRegistration:
        return self.__registration

    def preauthorizes(self, candidate: ProductionCandidateSubject) -> bool:
        return self.__valid_now() and self.__matching_grant(candidate) is not None

    def scope_grant(self, scope: RepositoryScope) -> ProductionScopeGrant | None:
        if type(scope) is not RepositoryScope:
            raise TypeError("production scope lookup requires an exact repository scope")
        return next(
            (item for item in self.__receipt.scope_grants if item.subject.scope == scope), None
        )

    def authorize(
        self,
        subject: ProductionPlanSubject,
        *,
        current_evidence: CurrentProductionEvidence | None = None,
    ) -> AuthorizedProductionAdmission | None:
        if type(subject) is not ProductionPlanSubject or not self.__valid_now():
            return None
        scope_grant = self.__matching_grant(subject.candidate)
        if (
            scope_grant is None
            or scope_grant.subject.target_registry_hash != subject.target_registry_hash
            or (
                current_evidence is not None
                and (
                    type(current_evidence) is not CurrentProductionEvidence
                    or current_evidence.candidate != subject.candidate
                    or current_evidence.scope_grant != scope_grant
                )
            )
        ):
            return None
        return AuthorizedProductionAdmission(
            _AUTHORIZATION_TOKEN,
            authority_id=self.__authority_id,
            admission_subject_digest=scope_grant.admission_subject_digest,
            not_after=self.__receipt.expires_at,
            subject=subject,
            current_evidence=current_evidence,
        )

    def __valid_now(self) -> bool:
        now = self.__clock.now()
        if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
            return False
        now = now.astimezone(UTC)
        return (
            self.__receipt.issued_at <= now + _MAX_CLOCK_SKEW
            and now + timedelta(seconds=self.__minimum_remaining_seconds)
            < self.__receipt.expires_at
        )

    def __matching_grant(
        self,
        candidate: ProductionCandidateSubject,
    ) -> ProductionScopeGrant | None:
        if type(candidate) is not ProductionCandidateSubject:
            return None
        scope_grant = next(
            (item for item in self.__receipt.scope_grants if item.subject.scope == candidate.scope),
            None,
        )
        if scope_grant is None:
            return None
        return scope_grant if scope_grant.subject.admits(candidate) else None


def _issue_production_admission_grant(
    *,
    authority_id: str,
    receipt: ProductionAdmissionReceipt,
    registration: ProductionAdmissionRegistration,
    clock: Clock,
    minimum_remaining_seconds: int,
) -> ProductionAdmissionGrant:
    return ProductionAdmissionGrant(
        _GRANT_TOKEN,
        authority_id=authority_id,
        receipt=receipt,
        registration=registration,
        clock=clock,
        minimum_remaining_seconds=minimum_remaining_seconds,
    )
