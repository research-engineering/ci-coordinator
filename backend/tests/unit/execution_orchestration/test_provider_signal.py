from __future__ import annotations

from dataclasses import replace

import pytest

from ci_coordinator.execution_orchestration import ProviderOccurrence, ProviderSignal


def test_provider_signal_is_derived_from_profile_and_content_addressed_shard() -> None:
    signal = ProviderSignal.derive(
        execution_profile_id="python-313",
        shard_id="ci_shard_0123456789abcdef0123456789abcdef",
    )

    assert signal.signal_id.startswith("provider_signal_")
    assert signal.job_name.startswith("ci/python-313/")
    assert signal == ProviderSignal.derive(
        execution_profile_id="python-313",
        shard_id="ci_shard_0123456789abcdef0123456789abcdef",
    )


def test_provider_signal_rejects_supplied_identity_or_job_name() -> None:
    signal = ProviderSignal.derive(
        execution_profile_id="python-313",
        shard_id="ci_shard_0123456789abcdef0123456789abcdef",
    )

    with pytest.raises(ValueError, match="derived identity"):
        replace(signal, job_name="configured-success-name")


def test_provider_occurrence_is_bound_to_exact_run_attempt() -> None:
    occurrence = ProviderOccurrence(
        signal=ProviderSignal.derive(
            execution_profile_id="python-313",
            shard_id="ci_shard_0123456789abcdef0123456789abcdef",
        ),
        workflow_run_id=41,
        run_attempt=2,
        job_id=73,
    )

    assert occurrence.belongs_to(workflow_run_id=41, run_attempt=2)
    assert not occurrence.belongs_to(workflow_run_id=41, run_attempt=1)
