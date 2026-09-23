from collections.abc import Mapping
from typing import Literal

from sqlalchemy.engine import RowMapping

from ci_coordinator.ci_economics.archive_retention import (
    ArchiveDetailRetention,
    ArchiveDetailState,
    DetailPolicyReference,
    DetailPolicySource,
    DetailRetentionPolicy,
)
from ci_coordinator.ci_economics.archive_retention_payload import DETAIL_RETENTION_ADAPTER
from ci_coordinator.ci_economics.archive_statistics import ArchiveInstant
from ci_coordinator.ci_economics.history_configuration import HistoryDefaults
from ci_coordinator.ci_economics.observation_payload import ObservationPositiveId
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
)

MAX_HISTORY_POLICY_BYTES = 1024


class _RetentionColumns(EconomicsPayloadModel):
    detail_state: ArchiveDetailState
    detail_first_imported_at: ArchiveInstant | None
    detail_policy_source: DetailPolicySource | None
    detail_policy_revision: ObservationPositiveId | None
    detail_expires_at: ArchiveInstant | None


class _DefaultsColumns(EconomicsPayloadModel):
    singleton: Literal[True]
    revision: ObservationPositiveId
    updated_at: ArchiveInstant


def encode_history_policy(policy: DetailRetentionPolicy) -> bytes:
    if type(policy) is not DetailRetentionPolicy:
        raise TypeError("history policy persistence requires an exact policy")
    return encode_canonical_object(
        policy.canonical_mapping(), maximum_bytes=MAX_HISTORY_POLICY_BYTES, context="history policy"
    )


def decode_history_policy(raw: object) -> DetailRetentionPolicy:
    mapping = decode_canonical_object(
        raw, maximum_bytes=MAX_HISTORY_POLICY_BYTES, context="history policy"
    )
    return DETAIL_RETENTION_ADAPTER.validate_python(mapping).to_policy()


def encode_history_retention(retention: ArchiveDetailRetention) -> dict[str, object]:
    if type(retention) is not ArchiveDetailRetention:
        raise TypeError("history retention persistence requires an exact value")
    reference = retention.applied_reference
    return {
        "detail_state": retention.state,
        "detail_first_imported_at": retention.first_imported_at,
        "detail_policy_canonical": None
        if retention.applied_policy is None
        else encode_history_policy(retention.applied_policy),
        "detail_policy_source": None if reference is None else reference.source,
        "detail_policy_revision": None if reference is None else reference.revision,
        "detail_expires_at": retention.expires_at,
    }


def decode_history_retention(row: Mapping[str, object] | RowMapping) -> ArchiveDetailRetention:
    columns = _RetentionColumns.model_validate(
        {name: row.get(name) for name in _RetentionColumns.model_fields}
    )
    raw = row.get("detail_policy_canonical")
    reference = None
    if columns.detail_policy_source is not None or columns.detail_policy_revision is not None:
        if columns.detail_policy_source is None or columns.detail_policy_revision is None:
            raise ValueError("history applied policy reference is incomplete")
        reference = DetailPolicyReference(
            columns.detail_policy_source, columns.detail_policy_revision
        )
    retention = ArchiveDetailRetention(
        columns.detail_state,
        columns.detail_first_imported_at,
        None if raw is None else decode_history_policy(raw),
        reference,
    )
    if columns.detail_expires_at != retention.expires_at:
        raise ValueError("history expiry projection differs from its applied policy")
    return retention


def encode_history_defaults(defaults: HistoryDefaults) -> dict[str, object]:
    if type(defaults) is not HistoryDefaults:
        raise TypeError("history defaults persistence requires an exact value")
    return {
        "singleton": True,
        "revision": defaults.revision,
        "detail_policy_canonical": encode_history_policy(defaults.detail_retention),
        "updated_at": defaults.updated_at,
    }


def decode_history_defaults(row: Mapping[str, object] | RowMapping) -> HistoryDefaults:
    if row.get("singleton") is not True:
        raise ValueError("history defaults require the exact singleton identity")
    columns = _DefaultsColumns.model_validate(
        {name: row.get(name) for name in _DefaultsColumns.model_fields}
    )
    return HistoryDefaults(
        columns.revision,
        decode_history_policy(row.get("detail_policy_canonical")),
        columns.updated_at,
    )
