from __future__ import annotations

import gzip
import json
from copy import deepcopy
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

import pytest
from schemathesis import Case
from schemathesis.core import NOT_SET
from schemathesis.core.failures import FailureGroup
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    CoverageScenario,
    GenerationInfo,
    PhaseInfo,
)

from .case_record import decode_case, encode_case
from .diagnostics import retain_case
from .harness import Harness
from .oracles import validate_response
from .ports import Ports
from .profile import HEADERS, REQUEST_TIMEOUT_SECONDS, VALIDATION_PATH, WORKBENCH_PATH
from .test_controls import positive_case


def metadata_case(harness: Harness, path: str) -> Case[Any]:
    original = positive_case(harness, path)
    location = ParameterLocation.BODY if path == VALIDATION_PATH else ParameterLocation.QUERY
    metadata = CaseMetadata(
        generation=GenerationInfo(time=0.125, mode=GenerationMode.POSITIVE),
        components={location: ComponentInfo(mode=GenerationMode.POSITIVE)},
        phase=PhaseInfo.coverage(
            CoverageScenario.DEFAULT_POSITIVE_TEST,
            "Exact replay control",
            location="/properties/source" if path == VALIDATION_PATH else "/limit",
            parameter="source" if path == VALIDATION_PATH else "limit",
            parameter_location=location,
        ),
        raw_containers={
            location: deepcopy(original.body if path == VALIDATION_PATH else original.query)
        },
    )
    return cast(
        Case[Any],
        original.operation.Case(
            path_parameters=original.path_parameters,
            query=original.query,
            body=original.body,
            media_type=original.media_type,
            _meta=metadata,
        ),
    )


@pytest.mark.parametrize("path", [WORKBENCH_PATH, VALIDATION_PATH])
def test_encoder_retention_decoder_preserves_exact_case(
    harness: Harness, tmp_path: Path, path: str
) -> None:
    case = metadata_case(harness, path)
    case.headers["X-Replay-Control"] = "synthetic"
    case.cookies["replay-control"] = "synthetic"
    record = encode_case(case, harness)
    data = json.loads(record)
    assert data["schema_sha256"] == harness.schema_digest
    assert data["profile"] == harness.campaign.name
    assert data["seed"] == harness.campaign.seed
    assert data["schemathesis"] == version("schemathesis")
    assert data["hypothesis"] == version("hypothesis")
    assert data["operation"] == case.operation.label
    assert data["phase"] == "coverage"
    assert data["transport_headers"] == HEADERS
    assert data["body_present"] is (path == VALIDATION_PATH)
    assert case.meta is not None
    assert data["metadata"] == case.meta.to_dict()
    note = retain_case(record, tmp_path)
    artifact = tmp_path / f"case-{sha256(record).hexdigest()}.json.gz"
    assert str(artifact) in note
    stored = gzip.decompress(artifact.read_bytes())
    assert stored == record
    replay = decode_case(stored, harness)
    assert replay == case
    assert replay.operation.app is harness.app
    assert replay.meta is not None
    assert replay.meta.to_dict() == case.meta.to_dict()
    assert encode_case(replay, harness) == record


@pytest.mark.parametrize("path", [WORKBENCH_PATH, VALIDATION_PATH])
@pytest.mark.parametrize("violation", ["cache", "body"])
def test_generated_failure_artifact_replays_same_oracle_then_healthy_control(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, violation: str
) -> None:
    from . import test_generated as generated

    case = metadata_case(harness, path)
    failures: list[tuple[str, ...]] = []

    def corrupt_response(
        candidate: Case[Any], healthy: Response, ports: Ports, *, trusted_machine: bool = False
    ) -> None:
        assert healthy.status_code == 200
        validate_response(candidate, healthy, ports, trusted_machine=trusted_machine)
        payload = deepcopy(healthy.json())
        headers = deepcopy(healthy.headers)
        if violation == "cache":
            headers["cache-control"] = ["public"]
        else:
            payload["ok"] = "not-a-boolean"
        corrupted = Response(
            status_code=healthy.status_code,
            headers=headers,
            content=json.dumps(payload).encode(),
            request=healthy.request,
            elapsed=healthy.elapsed,
            verify=healthy.verify,
        )
        assert healthy.json() == json.loads(healthy.content)
        assert healthy.headers["cache-control"] == ["no-store"]
        try:
            validate_response(candidate, corrupted, ports, trusted_machine=trusted_machine)
        except FailureGroup as error:
            kinds = tuple(sorted(type(item).__name__ for item in error.exceptions))
            assert violation == "body" and "JsonSchemaError" in kinds
            failures.append(kinds)
            raise
        except AssertionError as error:
            assert violation == "cache" and str(error) == "Missing no-store"
            failures.append((str(error),))
            raise
        pytest.fail("Visible response violation was not detected")

    monkeypatch.setattr(generated, "HARNESS", harness)
    monkeypatch.setattr(generated, "validate_response", corrupt_response)
    monkeypatch.setenv("CI_COORDINATOR_API_ARTIFACTS", str(tmp_path))
    expected_record = encode_case(case, harness)
    failure_class = AssertionError if violation == "cache" else FailureGroup
    with pytest.raises(failure_class) as original_failure:
        generated.test_api_contract(case)
    assert len(failures) == 1
    artifact = tmp_path / f"case-{sha256(expected_record).hexdigest()}.json.gz"
    assert any(str(artifact) in note for note in original_failure.value.__notes__)
    stored = gzip.decompress(artifact.read_bytes())
    assert stored == expected_record
    replay = decode_case(stored, harness)
    assert replay.meta is not None and case.meta is not None
    assert replay.meta.to_dict() == case.meta.to_dict()
    assert replay.body == case.body
    with pytest.raises(failure_class) as replay_failure:
        generated.test_api_contract(replay)
    assert len(failures) == 2 and failures[0] == failures[1]
    assert any(str(artifact) in note for note in replay_failure.value.__notes__)
    assert list(tmp_path.glob("case-*.json.gz")) == [artifact]
    monkeypatch.setattr(generated, "validate_response", validate_response)
    generated.test_api_contract(replay)
    assert len(failures) == 2
    with harness.example() as client:
        healthy = replay.call(
            session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS
        )
        assert healthy.status_code == 200
        validate_response(replay, healthy, harness.ports)


@pytest.mark.parametrize("body", [NOT_SET, None, b"", b"\x00\xff\x80binary\r\n"])
def test_body_presence_and_binary_record_roundtrip(
    harness: Harness, tmp_path: Path, body: Any
) -> None:
    case = harness.schema[VALIDATION_PATH]["POST"].Case(body=body, media_type="application/json")
    record = encode_case(case, harness)
    note = retain_case(record, tmp_path)
    artifact = tmp_path / f"case-{sha256(record).hexdigest()}.json.gz"
    assert str(artifact) in note
    replay = decode_case(gzip.decompress(artifact.read_bytes()), harness)
    assert (replay.body is NOT_SET) is (body is NOT_SET)
    assert replay.body == body
    assert type(replay.body) is type(body)
    assert replay.meta is None
    assert encode_case(replay, harness) == record


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_sha256", "0" * 64),
        ("profile", "unadmitted"),
        ("seed", -1),
        ("schemathesis", "0.0.0"),
        ("hypothesis", "0.0.0"),
        ("transport_headers", {}),
        ("path", "https://live.example/api"),
        ("method", "DELETE"),
        ("operation", "GET /unadmitted"),
        ("phase", "fuzzing"),
        ("body_present", "false"),
        ("body_encoding", "unknown"),
    ],
)
def test_decoder_rejects_mismatched_replay_identity(
    harness: Harness, field: str, value: object
) -> None:
    data = json.loads(encode_case(metadata_case(harness, VALIDATION_PATH), harness))
    data[field] = value
    with pytest.raises(ValueError, match="Exact case"):
        decode_case(json.dumps(data).encode(), harness)
    assert harness.ports.starts == harness.ports.stops == 0
    assert not harness.ports.authentications


@pytest.mark.parametrize(
    "present,encoding,body", [(False, "json", {}), (False, "base64", None), (True, "base64", 1)]
)
def test_decoder_rejects_inconsistent_body_record(
    harness: Harness, present: bool, encoding: str, body: object
) -> None:
    data = json.loads(encode_case(positive_case(harness, VALIDATION_PATH), harness))
    data.update(body_present=present, body_encoding=encoding, body=body)
    with pytest.raises(ValueError, match="Exact case"):
        decode_case(json.dumps(data).encode(), harness)
