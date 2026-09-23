"""Stable facade for canonical reconciliation persistence codecs."""

from __future__ import annotations

from ci_coordinator.persistence._reconciliation_codec_support import (
    ReconciliationStateCodecError,
)
from ci_coordinator.persistence.reconciliation_observation_codec import (
    decode_observation_row,
    encode_observation_row,
)
from ci_coordinator.persistence.reconciliation_result_codec import (
    decode_result_row,
    encode_result_row,
)
from ci_coordinator.persistence.reconciliation_subject_codec import (
    decode_subject_row,
    encode_subject_row,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)
from ci_coordinator.reconciliation import (
    ReconciliationConvergenceState,
    ReconciliationSnapshot,
)


def snapshot_from_rows(
    subject_row: dict[str, object],
    observation_rows: tuple[dict[str, object], ...],
    profile: ShadowReconciliationStateProfile,
) -> tuple[ReconciliationSnapshot, ReconciliationConvergenceState]:
    subject, contract, revision, convergence = decode_subject_row(subject_row, profile)
    decoded = tuple(decode_observation_row(row, subject, profile) for row in observation_rows)
    observed_revisions = tuple(item[0] for item in decoded)
    if observed_revisions != tuple(range(1, revision + 1)):
        raise ReconciliationStateCodecError(
            "stored reconciliation observation revisions are invalid"
        )
    try:
        return (
            ReconciliationSnapshot(
                subject=subject,
                contract=contract,
                revision=revision,
                observations=tuple(item[1] for item in decoded),
            ),
            convergence,
        )
    except (TypeError, ValueError) as error:
        raise ReconciliationStateCodecError("stored reconciliation snapshot is invalid") from error


__all__ = [
    "ReconciliationStateCodecError",
    "decode_observation_row",
    "decode_result_row",
    "decode_subject_row",
    "encode_observation_row",
    "encode_result_row",
    "encode_subject_row",
    "snapshot_from_rows",
]
