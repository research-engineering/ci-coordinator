from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime

import pytest

from ci_coordinator.identity_admission import (
    ExpectedActionsOidcClaims,
    RejectedIdentity,
    TrustedActionsRun,
    admit_verified_actions_oidc_claims,
    workflow_path_from_ref,
    workflow_path_identity_from_ref,
)
from ci_coordinator.kernel import FixedClock, hash_object

NOW = datetime(2026, 7, 9, 15, 0, tzinfo=UTC)
EXECUTION_SHA = "c" * 40
EXPECTED = ExpectedActionsOidcClaims(
    issuer="https://token.actions.githubusercontent.com",
    audience="ci-coordinator",
    repository="example/ci",
    repository_id=2002,
    ref="refs/pull/42/merge",
    run_id=7001,
    run_attempt=1,
    event_name="pull_request",
    expected_execution_sha=EXECUTION_SHA,
    allowed_workflow_refs=("example/ci/.github/workflows/dynamic-ci.yml@refs/heads/master",),
)


def test_admit_verified_actions_oidc_claims_accepts_matching_claims() -> None:
    claims = valid_claims()
    trusted = admit_verified_actions_oidc_claims(claims, EXPECTED, FixedClock(NOW))

    assert isinstance(trusted, TrustedActionsRun)
    assert trusted.issuer == "https://token.actions.githubusercontent.com"
    assert trusted.audience == "ci-coordinator"
    assert trusted.repository == "example/ci"
    assert trusted.repository_id == 2002
    assert trusted.ref == "refs/pull/42/merge"
    assert trusted.run_id == 7001
    assert trusted.run_attempt == 1
    assert trusted.event_name == "pull_request"
    assert trusted.execution_sha == EXECUTION_SHA
    assert trusted.workflow_ref == "example/ci/.github/workflows/dynamic-ci.yml@refs/heads/master"
    assert trusted.workflow_sha == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert trusted.job_workflow_ref is None
    assert trusted.job_workflow_sha is None
    assert trusted.check_run_id == "9001"
    assert trusted.verified_at == NOW
    assert trusted.claim_hash == hash_object(claims)
    assert not hasattr(trusted, "jwt")
    assert not hasattr(trusted, "raw_token")


def test_admit_verified_actions_oidc_claims_reads_from_one_claim_snapshot() -> None:
    claims = SnapshotThenMutatingClaims(valid_claims(), {"repository": "example/mutated"})

    trusted = admit_verified_actions_oidc_claims(claims, EXPECTED, FixedClock(NOW))

    assert isinstance(trusted, TrustedActionsRun)
    assert trusted.repository == "example/ci"
    assert trusted.claim_hash == hash_object(valid_claims())


def test_admit_verified_actions_oidc_claims_accepts_array_audience() -> None:
    claims = valid_claims()
    claims["aud"] = ["other-audience", "ci-coordinator"]

    trusted = admit_verified_actions_oidc_claims(claims, EXPECTED, FixedClock(NOW))

    assert isinstance(trusted, TrustedActionsRun)
    assert trusted.audience == "ci-coordinator"


def test_admit_verified_actions_oidc_claims_accepts_allowed_job_workflow_identity() -> None:
    claims = valid_claims()
    claims["workflow_ref"] = None
    claims["job_workflow_ref"] = "example/ci/.github/workflows/reusable.yml@refs/heads/master"
    expected = ExpectedActionsOidcClaims(
        issuer=EXPECTED.issuer,
        audience=EXPECTED.audience,
        repository=EXPECTED.repository,
        repository_id=EXPECTED.repository_id,
        ref=EXPECTED.ref,
        run_id=EXPECTED.run_id,
        run_attempt=EXPECTED.run_attempt,
        event_name=EXPECTED.event_name,
        expected_execution_sha=EXPECTED.expected_execution_sha,
        allowed_workflow_refs=(),
        allowed_job_workflow_refs=("example/ci/.github/workflows/reusable.yml@refs/heads/master",),
    )

    trusted = admit_verified_actions_oidc_claims(claims, expected, FixedClock(NOW))

    assert isinstance(trusted, TrustedActionsRun)
    assert trusted.workflow_ref is None
    assert trusted.job_workflow_ref == "example/ci/.github/workflows/reusable.yml@refs/heads/master"


@pytest.mark.parametrize(
    ("claim_name", "sha_name"),
    [
        ("workflow_ref", "workflow_sha"),
        ("job_workflow_ref", "job_workflow_sha"),
    ],
    ids=["top-level", "reusable-job"],
)
def test_admit_verified_actions_oidc_claims_accepts_revision_bound_workflow_path(
    claim_name: str,
    sha_name: str,
) -> None:
    claims = valid_claims()
    claims["workflow_ref"] = None
    claims["job_workflow_ref"] = None
    claims[claim_name] = "example/ci/.github/workflows/dynamic-ci.yml@refs/pull/42/merge"
    claims[sha_name] = "b" * 40
    expected = ExpectedActionsOidcClaims(
        issuer=EXPECTED.issuer,
        audience=EXPECTED.audience,
        repository=EXPECTED.repository,
        repository_id=EXPECTED.repository_id,
        ref=EXPECTED.ref,
        run_id=EXPECTED.run_id,
        run_attempt=EXPECTED.run_attempt,
        event_name=EXPECTED.event_name,
        expected_execution_sha=EXPECTED.expected_execution_sha,
        allowed_workflow_refs=(),
        allowed_workflow_paths=(
            ("example/ci/.github/workflows/dynamic-ci.yml",) if claim_name == "workflow_ref" else ()
        ),
        allowed_job_workflow_paths=(
            ("example/ci/.github/workflows/dynamic-ci.yml",)
            if claim_name == "job_workflow_ref"
            else ()
        ),
    )

    trusted = admit_verified_actions_oidc_claims(claims, expected, FixedClock(NOW))

    assert isinstance(trusted, TrustedActionsRun)


@pytest.mark.parametrize("workflow_sha", [None, "", "A" * 40, "a" * 39, "a" * 65])
def test_path_admitted_oidc_identity_requires_canonical_immutable_workflow_sha(
    workflow_sha: str | None,
) -> None:
    claims = valid_claims()
    claims["workflow_ref"] = "example/ci/.github/workflows/dynamic-ci.yml@refs/pull/42/merge"
    claims["workflow_sha"] = workflow_sha
    expected = ExpectedActionsOidcClaims(
        issuer=EXPECTED.issuer,
        audience=EXPECTED.audience,
        repository=EXPECTED.repository,
        repository_id=EXPECTED.repository_id,
        ref=EXPECTED.ref,
        run_id=EXPECTED.run_id,
        run_attempt=EXPECTED.run_attempt,
        event_name=EXPECTED.event_name,
        expected_execution_sha=EXPECTED.expected_execution_sha,
        allowed_workflow_refs=(),
        allowed_workflow_paths=("example/ci/.github/workflows/dynamic-ci.yml",),
    )

    rejected = admit_verified_actions_oidc_claims(claims, expected, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "oidc_workflow_sha_required"


def test_path_admission_does_not_broaden_repository_or_workflow_identity() -> None:
    claims = valid_claims()
    claims["workflow_ref"] = "example/ci/.github/workflows/other.yml@refs/pull/42/merge"
    expected = ExpectedActionsOidcClaims(
        issuer=EXPECTED.issuer,
        audience=EXPECTED.audience,
        repository=EXPECTED.repository,
        repository_id=EXPECTED.repository_id,
        ref=EXPECTED.ref,
        run_id=EXPECTED.run_id,
        run_attempt=EXPECTED.run_attempt,
        event_name=EXPECTED.event_name,
        expected_execution_sha=EXPECTED.expected_execution_sha,
        allowed_workflow_refs=(),
        allowed_workflow_paths=("example/ci/.github/workflows/dynamic-ci.yml",),
    )

    rejected = admit_verified_actions_oidc_claims(claims, expected, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "oidc_workflow_not_allowed"


def test_admit_verified_actions_oidc_claims_rejects_disallowed_job_workflow_identity() -> None:
    claims = valid_claims()
    claims["workflow_ref"] = None
    claims["job_workflow_ref"] = "example/ci/.github/workflows/other-reusable.yml@refs/heads/master"
    expected = ExpectedActionsOidcClaims(
        issuer=EXPECTED.issuer,
        audience=EXPECTED.audience,
        repository=EXPECTED.repository,
        repository_id=EXPECTED.repository_id,
        ref=EXPECTED.ref,
        run_id=EXPECTED.run_id,
        run_attempt=EXPECTED.run_attempt,
        event_name=EXPECTED.event_name,
        expected_execution_sha=EXPECTED.expected_execution_sha,
        allowed_workflow_refs=(),
        allowed_job_workflow_refs=("example/ci/.github/workflows/reusable.yml@refs/heads/master",),
    )

    rejected = admit_verified_actions_oidc_claims(claims, expected, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "oidc_workflow_not_allowed"


def test_admit_verified_actions_oidc_claims_requires_both_configured_identities() -> None:
    claims = valid_claims()
    claims["workflow_ref"] = "example/ci/.github/workflows/other.yml@refs/heads/master"
    claims["job_workflow_ref"] = "example/ci/.github/workflows/reusable.yml@refs/heads/master"
    expected = ExpectedActionsOidcClaims(
        issuer=EXPECTED.issuer,
        audience=EXPECTED.audience,
        repository=EXPECTED.repository,
        repository_id=EXPECTED.repository_id,
        ref=EXPECTED.ref,
        run_id=EXPECTED.run_id,
        run_attempt=EXPECTED.run_attempt,
        event_name=EXPECTED.event_name,
        expected_execution_sha=EXPECTED.expected_execution_sha,
        allowed_workflow_refs=EXPECTED.allowed_workflow_refs,
        allowed_job_workflow_refs=("example/ci/.github/workflows/reusable.yml@refs/heads/master",),
    )

    rejected = admit_verified_actions_oidc_claims(claims, expected, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "oidc_workflow_not_allowed"


def test_admit_verified_actions_oidc_claims_rejects_missing_required_claims() -> None:
    cases: list[tuple[str, str]] = [
        ("iss", "oidc_issuer_mismatch"),
        ("aud", "oidc_audience_mismatch"),
        ("exp", "oidc_token_expired"),
        ("repository", "oidc_repository_mismatch"),
        ("repository_id", "oidc_repository_id_mismatch"),
        ("ref", "oidc_ref_mismatch"),
        ("run_id", "oidc_run_identity_mismatch"),
        ("run_attempt", "oidc_run_identity_mismatch"),
        ("event_name", "oidc_event_name_mismatch"),
        ("sha", "oidc_execution_sha_mismatch"),
        ("workflow_ref", "oidc_workflow_not_allowed"),
    ]
    for key, reason_code in cases:
        claims = valid_claims()
        del claims[key]

        rejected = admit_verified_actions_oidc_claims(claims, EXPECTED, FixedClock(NOW))

        assert isinstance(rejected, RejectedIdentity), key
        assert rejected.reason_code == reason_code


def test_admit_verified_actions_oidc_claims_rejects_claim_mismatch_matrix() -> None:
    cases: list[tuple[str, object, str]] = [
        ("iss", "https://example.invalid", "oidc_issuer_mismatch"),
        ("aud", "other-audience", "oidc_audience_mismatch"),
        ("repository", "example/other", "oidc_repository_mismatch"),
        ("repository_id", "9999", "oidc_repository_id_mismatch"),
        ("ref", "refs/heads/master", "oidc_ref_mismatch"),
        ("run_id", "8001", "oidc_run_identity_mismatch"),
        ("run_attempt", "2", "oidc_run_identity_mismatch"),
        ("event_name", "push", "oidc_event_name_mismatch"),
        ("sha", "d" * 40, "oidc_execution_sha_mismatch"),
        (
            "workflow_ref",
            "example/ci/.github/workflows/other.yml@refs/heads/master",
            "oidc_workflow_not_allowed",
        ),
    ]
    for key, value, reason_code in cases:
        claims = valid_claims()
        claims[key] = value

        rejected = admit_verified_actions_oidc_claims(claims, EXPECTED, FixedClock(NOW))

        assert isinstance(rejected, RejectedIdentity), key
        assert rejected.reason_code == reason_code
        assert rejected.claim_hash == hash_object(claims)


def test_admit_verified_actions_oidc_claims_keeps_safe_integer_semantics() -> None:
    invalid_cases: list[tuple[str, object, str]] = [
        ("repository_id", 2002, "oidc_repository_id_mismatch"),
        ("repository_id", "2002.0", "oidc_repository_id_mismatch"),
        ("repository_id", "-2002", "oidc_repository_id_mismatch"),
        ("repository_id", "\uff12" + "002", "oidc_repository_id_mismatch"),
        ("repository_id", "9007199254740992", "oidc_repository_id_mismatch"),
        ("run_id", 7001, "oidc_run_identity_mismatch"),
    ]
    for key, value, reason_code in invalid_cases:
        claims = valid_claims()
        claims[key] = value

        rejected = admit_verified_actions_oidc_claims(claims, EXPECTED, FixedClock(NOW))

        assert isinstance(rejected, RejectedIdentity), key
        assert rejected.reason_code == reason_code

    leading_zero_claims = valid_claims()
    leading_zero_claims["repository_id"] = "02002"
    leading_zero_claims["run_id"] = "07001"
    leading_zero_claims["run_attempt"] = "01"

    trusted = admit_verified_actions_oidc_claims(leading_zero_claims, EXPECTED, FixedClock(NOW))

    assert isinstance(trusted, TrustedActionsRun)
    assert trusted.repository_id == 2002
    assert trusted.run_id == 7001
    assert trusted.run_attempt == 1


def test_admit_verified_actions_oidc_claims_rejects_expired_or_not_yet_valid_claims() -> None:
    expired = valid_claims()
    expired["exp"] = int(NOW.timestamp())
    rejected_expired = admit_verified_actions_oidc_claims(expired, EXPECTED, FixedClock(NOW))

    not_yet_valid = valid_claims()
    not_yet_valid["nbf"] = int(NOW.timestamp()) + 60
    rejected_not_yet_valid = admit_verified_actions_oidc_claims(
        not_yet_valid,
        EXPECTED,
        FixedClock(NOW),
    )

    assert isinstance(rejected_expired, RejectedIdentity)
    assert rejected_expired.reason_code == "oidc_token_expired"
    assert isinstance(rejected_not_yet_valid, RejectedIdentity)
    assert rejected_not_yet_valid.reason_code == "oidc_token_not_yet_valid"


def test_admit_verified_actions_oidc_claims_accepts_absent_or_equal_not_before() -> None:
    equal_not_before = valid_claims()
    equal_not_before["nbf"] = int(NOW.timestamp())
    absent_not_before = valid_claims()
    del absent_not_before["nbf"]

    trusted_equal = admit_verified_actions_oidc_claims(equal_not_before, EXPECTED, FixedClock(NOW))
    trusted_absent = admit_verified_actions_oidc_claims(
        absent_not_before,
        EXPECTED,
        FixedClock(NOW),
    )

    assert isinstance(trusted_equal, TrustedActionsRun)
    assert isinstance(trusted_absent, TrustedActionsRun)


def test_admit_verified_actions_oidc_claims_enforces_configured_workflow_sha() -> None:
    claims = valid_claims()
    expected = ExpectedActionsOidcClaims(
        issuer=EXPECTED.issuer,
        audience=EXPECTED.audience,
        repository=EXPECTED.repository,
        repository_id=EXPECTED.repository_id,
        ref=EXPECTED.ref,
        run_id=EXPECTED.run_id,
        run_attempt=EXPECTED.run_attempt,
        event_name=EXPECTED.event_name,
        expected_execution_sha=EXPECTED.expected_execution_sha,
        allowed_workflow_refs=EXPECTED.allowed_workflow_refs,
        expected_workflow_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )

    rejected = admit_verified_actions_oidc_claims(claims, expected, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "oidc_workflow_sha_mismatch"


def test_admit_verified_actions_oidc_claims_enforces_configured_job_workflow_sha() -> None:
    claims = valid_claims()
    claims["workflow_ref"] = None
    claims["job_workflow_ref"] = "example/ci/.github/workflows/reusable.yml@refs/heads/master"
    claims["job_workflow_sha"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    expected = ExpectedActionsOidcClaims(
        issuer=EXPECTED.issuer,
        audience=EXPECTED.audience,
        repository=EXPECTED.repository,
        repository_id=EXPECTED.repository_id,
        ref=EXPECTED.ref,
        run_id=EXPECTED.run_id,
        run_attempt=EXPECTED.run_attempt,
        event_name=EXPECTED.event_name,
        expected_execution_sha=EXPECTED.expected_execution_sha,
        allowed_workflow_refs=(),
        allowed_job_workflow_refs=("example/ci/.github/workflows/reusable.yml@refs/heads/master",),
        expected_job_workflow_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )

    rejected = admit_verified_actions_oidc_claims(claims, expected, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "oidc_job_workflow_sha_mismatch"


def test_admit_verified_actions_oidc_claims_rejects_non_json_claims_without_raw_payload() -> None:
    claims = valid_claims()
    claims["non_json"] = ("tuple",)

    rejected = admit_verified_actions_oidc_claims(claims, EXPECTED, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "oidc_claims_not_json"
    assert rejected.claim_hash is None
    assert not hasattr(rejected, "jwt")
    assert not hasattr(rejected, "raw_token")
    assert not hasattr(rejected, "claims")


def test_admit_verified_actions_oidc_claims_rejects_without_echoing_claim_values() -> None:
    sentinel = "secret-claim-sentinel"
    claims = valid_claims()
    claims["repository"] = sentinel

    rejected = admit_verified_actions_oidc_claims(claims, EXPECTED, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    rendered = f"{rejected!r} {rejected.__dict__!r} {rejected.message}"
    assert sentinel not in rendered
    assert rejected.claim_hash == hash_object(claims)


def test_expected_actions_oidc_claims_rejects_invalid_verifier_configuration() -> None:
    with pytest.raises(ValueError, match="audience is required"):
        ExpectedActionsOidcClaims(
            issuer=EXPECTED.issuer,
            audience="",
            repository=EXPECTED.repository,
            repository_id=EXPECTED.repository_id,
            ref=EXPECTED.ref,
            run_id=EXPECTED.run_id,
            run_attempt=EXPECTED.run_attempt,
            event_name=EXPECTED.event_name,
            expected_execution_sha=EXPECTED.expected_execution_sha,
            allowed_workflow_refs=EXPECTED.allowed_workflow_refs,
        )

    with pytest.raises(ValueError, match="workflow identity is required"):
        ExpectedActionsOidcClaims(
            issuer=EXPECTED.issuer,
            audience=EXPECTED.audience,
            repository=EXPECTED.repository,
            repository_id=EXPECTED.repository_id,
            ref=EXPECTED.ref,
            run_id=EXPECTED.run_id,
            run_attempt=EXPECTED.run_attempt,
            event_name=EXPECTED.event_name,
            expected_execution_sha=EXPECTED.expected_execution_sha,
            allowed_workflow_refs=(),
            allowed_job_workflow_refs=(),
        )


@pytest.mark.parametrize(
    ("repository", "workflow_ref", "expected"),
    [
        (
            "example/ci",
            "example/ci/.github/workflows/dynamic-ci.yml@refs/heads/feature@v2",
            ".github/workflows/dynamic-ci.yml",
        ),
        ("example/ci", "other/ci/.github/workflows/dynamic-ci.yml@refs/heads/main", None),
        ("example/ci", "example/ci/.github/workflows/nested/ci.yml@refs/heads/main", None),
        ("example/ci", "example/ci/.github/workflows/ci.yml@main", None),
        ("example/ci", None, None),
    ],
    ids=["branch-at", "foreign-repository", "nested-path", "missing-refs", "absent"],
)
def test_workflow_path_projection_is_exact(
    repository: str,
    workflow_ref: str | None,
    expected: str | None,
) -> None:
    assert workflow_path_from_ref(repository=repository, workflow_ref=workflow_ref) == expected


@pytest.mark.parametrize(
    ("workflow_ref", "expected"),
    [
        (
            "example/ci/.github/workflows/dynamic-ci.yml@refs/pull/42/merge",
            "example/ci/.github/workflows/dynamic-ci.yml",
        ),
        ("example/ci/.github/workflows/nested/ci.yml@refs/heads/main", None),
        ("example/ci/.github/workflows/ci.yml@main", None),
        (None, None),
    ],
    ids=["pull-ref", "nested-path", "missing-refs", "absent"],
)
def test_workflow_path_identity_projection_is_exact(
    workflow_ref: str | None,
    expected: str | None,
) -> None:
    assert workflow_path_identity_from_ref(workflow_ref) == expected


def valid_claims() -> dict[str, object]:
    return {
        "iss": "https://token.actions.githubusercontent.com",
        "aud": "ci-coordinator",
        "exp": int(NOW.timestamp()) + 600,
        "nbf": int(NOW.timestamp()) - 60,
        "repository": "example/ci",
        "repository_id": "2002",
        "ref": "refs/pull/42/merge",
        "run_id": "7001",
        "run_attempt": "1",
        "event_name": "pull_request",
        "sha": EXECUTION_SHA,
        "workflow_ref": "example/ci/.github/workflows/dynamic-ci.yml@refs/heads/master",
        "workflow_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "job_workflow_ref": None,
        "job_workflow_sha": None,
        "check_run_id": "9001",
    }


class SnapshotThenMutatingClaims(Mapping[str, object]):
    def __init__(self, snapshot: dict[str, object], changed_values: dict[str, object]) -> None:
        self._snapshot = snapshot
        self._changed_values = changed_values
        self._snapshotted = False

    def __getitem__(self, key: str) -> object:
        if self._snapshotted and key in self._changed_values:
            return self._changed_values[key]
        return self._snapshot[key]

    def __iter__(self) -> Iterator[str]:
        try:
            yield from self._snapshot
        finally:
            self._snapshotted = True

    def __len__(self) -> int:
        return len(self._snapshot)
