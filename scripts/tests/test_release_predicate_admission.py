from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from scripts.release_predicate_admission import (
    PredicateAdmissionError,
    admit_release_predicates,
)

SCHEMA_DIRECTORY = Path(__file__).resolve().parents[2] / "docs/specs/ci-coordinator-release"


def _provenance() -> dict[str, object]:
    return {
        "buildDefinition": {
            "buildType": (
                "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md"
            ),
            "externalParameters": {
                "configSource": {"path": "Dockerfile"},
                "request": {
                    "args": {"build-arg:CI_COORDINATOR_PRODUCTION_BUILD": "true"},
                    "frontend": "dockerfile.v0",
                    "locals": [{"name": "context"}, {"name": "dockerfile"}],
                },
            },
            "internalParameters": {
                "buildConfig": {"llbDefinition": [{"id": "step0"}]},
                "builderPlatform": "linux/amd64",
            },
            "resolvedDependencies": [
                {
                    "digest": {"sha256": "a" * 64},
                    "uri": "pkg:docker/python@sha256:example?platform=linux%2Famd64",
                }
            ],
        },
        "runDetails": {
            "builder": {"id": ""},
            "metadata": {
                "buildkit_completeness": {
                    "request": True,
                    "resolvedDependencies": False,
                },
                "buildkit_metadata": {},
                "finishedOn": "2026-07-28T10:01:00Z",
                "invocationId": "build-1",
                "startedOn": "2026-07-28T10:00:00Z",
            },
        },
    }


def _sbom() -> dict[str, object]:
    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {
            "created": "2026-07-28T10:01:00Z",
            "creators": ["Tool: BuildKit"],
        },
        "dataLicense": "CC0-1.0",
        "documentNamespace": "https://example.invalid/spdx/build-1",
        "name": "ci-coordinator",
        "packages": [
            {
                "SPDXID": "SPDXRef-Package-ci-coordinator",
                "downloadLocation": "NOASSERTION",
                "name": "ci-coordinator",
            }
        ],
        "spdxVersion": "SPDX-2.3",
    }


def _write(path: Path, value: object) -> bytes:
    content = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    path.write_bytes(content)
    return content


def _admit(
    tmp_path: Path,
    *,
    provenance: object | None = None,
    sbom: object | None = None,
    schema_directory: Path = SCHEMA_DIRECTORY,
) -> None:
    provenance_path = tmp_path / "provenance.json"
    sbom_path = tmp_path / "sbom.json"
    provenance_raw = _write(
        provenance_path,
        _provenance() if provenance is None else provenance,
    )
    sbom_raw = _write(sbom_path, _sbom() if sbom is None else sbom)

    admission = admit_release_predicates(
        provenance_path=provenance_path,
        sbom_path=sbom_path,
        schema_directory=schema_directory,
    )

    assert admission.provenance_sha256 == hashlib.sha256(provenance_raw).hexdigest()
    assert admission.sbom_sha256 == hashlib.sha256(sbom_raw).hexdigest()


def test_predicate_admission_accepts_exact_profiles_and_hashes_exact_bytes(
    tmp_path: Path,
) -> None:
    _admit(tmp_path)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda value: cast(dict[str, object], value["buildDefinition"]).update(
                {"buildType": "https://mobyproject.org/buildkit@v1"}
            ),
            id="wrong-build-type",
        ),
        pytest.param(
            lambda value: cast(
                dict[str, object],
                cast(dict[str, object], value["buildDefinition"])["internalParameters"],
            ).update({"buildConfig": {"llbDefinition": []}}),
            id="missing-mode-max-evidence",
        ),
        pytest.param(
            lambda value: cast(dict[str, object], value["buildDefinition"]).update(
                {"resolvedDependencies": [{}]}
            ),
            id="empty-dependency",
        ),
        pytest.param(
            lambda value: cast(
                dict[str, object],
                cast(dict[str, object], value["runDetails"])["metadata"],
            ).update(
                {
                    "finishedOn": "2026-07-28T09:59:00Z",
                    "startedOn": "2026-07-28T10:00:00Z",
                }
            ),
            id="reversed-timestamps",
        ),
        pytest.param(
            lambda value: cast(
                dict[str, object],
                cast(dict[str, object], value["runDetails"])["metadata"],
            ).update({"startedOn": "2026-07-28"}),
            id="date-only-start",
        ),
        pytest.param(
            lambda value: cast(
                dict[str, object],
                cast(dict[str, object], value["runDetails"])["metadata"],
            ).update({"finishedOn": "not-a-date"}),
            id="invalid-finish",
        ),
    ],
)
def test_predicate_admission_rejects_each_invalid_provenance_dimension(
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], object],
) -> None:
    provenance = copy.deepcopy(_provenance())
    mutate(provenance)

    with pytest.raises(PredicateAdmissionError):
        _admit(tmp_path, provenance=provenance)


@pytest.mark.parametrize(
    "packages",
    [
        pytest.param([], id="empty"),
        pytest.param([{}], id="empty-package"),
        pytest.param(
            [
                {
                    "SPDXID": "SPDXRef-Package-duplicate",
                    "downloadLocation": "NOASSERTION",
                    "name": "first",
                },
                {
                    "SPDXID": "SPDXRef-Package-duplicate",
                    "downloadLocation": "NOASSERTION",
                    "name": "second",
                },
            ],
            id="duplicate-package-id",
        ),
    ],
)
def test_predicate_admission_rejects_non_meaningful_spdx_packages(
    tmp_path: Path,
    packages: list[object],
) -> None:
    sbom = _sbom()
    sbom["packages"] = packages

    with pytest.raises(PredicateAdmissionError):
        _admit(tmp_path, sbom=sbom)


@pytest.mark.parametrize("created", ["2026-07-28", "not-a-date"])
def test_predicate_admission_rejects_invalid_spdx_creation_time(
    tmp_path: Path,
    created: str,
) -> None:
    sbom = _sbom()
    cast(dict[str, object], sbom["creationInfo"])["created"] = created

    with pytest.raises(PredicateAdmissionError):
        _admit(tmp_path, sbom=sbom)


def test_predicate_admission_rejects_duplicate_json_members(tmp_path: Path) -> None:
    provenance_path = tmp_path / "provenance.json"
    sbom_path = tmp_path / "sbom.json"
    provenance_path.write_text('{"buildDefinition":{},"buildDefinition":{}}')
    _write(sbom_path, _sbom())

    with pytest.raises(PredicateAdmissionError, match="strict JSON"):
        admit_release_predicates(
            provenance_path=provenance_path,
            sbom_path=sbom_path,
            schema_directory=SCHEMA_DIRECTORY,
        )


def test_predicate_admission_rejects_substituted_spdx_schema(tmp_path: Path) -> None:
    schema_directory = tmp_path / "schemas"
    schema_directory.mkdir()
    for source in SCHEMA_DIRECTORY.glob("*.schema*.json"):
        (schema_directory / source.name).write_bytes(source.read_bytes())
    spdx_schema = schema_directory / "spdx-2.3.schema.json"
    spdx_schema.write_bytes(spdx_schema.read_bytes() + b"\n")

    with pytest.raises(PredicateAdmissionError, match="upstream bytes"):
        _admit(tmp_path, schema_directory=schema_directory)
