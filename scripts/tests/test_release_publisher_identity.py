from __future__ import annotations

import pytest
from scripts import release_publisher_identity as publisher


def test_source_owned_publisher_coordinates_are_exact() -> None:
    assert publisher.REPOSITORY == "research-engineering/ci-coordinator"
    assert publisher.IMAGE == "ghcr.io/research-engineering/ci-coordinator"
    assert publisher.WORKFLOW_PATH == ".github/workflows/release-artifact.yml"
    assert publisher.WORKFLOW_REF == (
        "research-engineering/ci-coordinator/.github/workflows/"
        "release-artifact.yml@refs/heads/master"
    )
    assert publisher.SIGNER_WORKFLOW == (
        "github.com/research-engineering/ci-coordinator/.github/workflows/release-artifact.yml"
    )
    assert publisher.REPAIR_PREDICATE == (
        "https://github.com/research-engineering/ci-coordinator/attestations/runtime-repairs/v1"
    )


@pytest.mark.parametrize(
    "name", ["CI_COORDINATOR_RELEASE_REPOSITORY_ID", "CI_COORDINATOR_FULL_CHECK_WORKFLOW_ID"]
)
@pytest.mark.parametrize("value", [None, "", "0", "-1", "+1", "01", " 1", "1\n", "1.0", "9" * 21])
def test_unconfigured_or_noncanonical_ids_emit_no_publisher_outputs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    value: str | None,
) -> None:
    monkeypatch.setenv("CI_COORDINATOR_RELEASE_REPOSITORY_ID", "1001")
    monkeypatch.setenv("CI_COORDINATOR_FULL_CHECK_WORKFLOW_ID", "2001")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1001")
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        publisher.main()
    assert capsys.readouterr().out == ""


def test_owner_configured_ids_are_read_without_import_time_defaults(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CI_COORDINATOR_RELEASE_REPOSITORY_ID", "1001")
    monkeypatch.setenv("CI_COORDINATOR_FULL_CHECK_WORKFLOW_ID", "2001")
    assert publisher.main() == 0
    assert dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines()) == {
        "repository": "research-engineering/ci-coordinator",
        "repository_id": "1001",
        "gate_workflow_id": "2001",
        "image": "ghcr.io/research-engineering/ci-coordinator",
        "workflow_ref": (
            "research-engineering/ci-coordinator/.github/workflows/"
            "release-artifact.yml@refs/heads/master"
        ),
        "signer_workflow": (
            "github.com/research-engineering/ci-coordinator/.github/workflows/release-artifact.yml"
        ),
        "repair_predicate": (
            "https://github.com/research-engineering/ci-coordinator/attestations/runtime-repairs/v1"
        ),
    }
    monkeypatch.setenv("CI_COORDINATOR_RELEASE_REPOSITORY_ID", "3001")
    assert publisher.repository_id() == 3001
