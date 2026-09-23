"""Re-admission proof for a durable config-epoch boundary."""

from __future__ import annotations

from ci_coordinator.config_control._admission import admit_policy_document
from ci_coordinator.config_control.contracts import ValidatedEpochDraft


class EpochDraftIntegrityError(ValueError):
    """A caller-supplied draft is not exactly reproducible by policy admission."""


def assert_admitted_epoch_draft(value: object) -> ValidatedEpochDraft:
    """Return an exact draft only when bounded pure admission reproduces it byte-for-byte."""
    if type(value) is not ValidatedEpochDraft:
        raise EpochDraftIntegrityError("config epoch registration requires an exact admitted draft")
    replayed = admit_policy_document(value.source_bytes, value.source_format)
    if type(replayed) is not ValidatedEpochDraft or replayed != value:
        raise EpochDraftIntegrityError("config epoch draft does not match policy admission")
    return value
