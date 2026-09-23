from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Final, cast

from ci_coordinator.audit_replay import (
    AuditEventError,
    AuditEventInput,
    build_audit_event,
)
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.audit_replay.persistence_bytes import (
    AUDIT_PERSISTENCE_BYTE_PROFILE_ID,
    MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
    MAX_AUDIT_TEXT_UTF8_BYTES_V1,
    admit_audit_text_bytes,
    canonical_audit_payload_bytes,
)

CORPUS_PATH: Final = Path("fixtures/conformance/v1/audit-persistence-byte-corpus.v1.json")
CORPUS_SCHEMA: Final = "ci-audit-persistence-byte-corpus/v1"
PROFILE_DIGEST: Final = "b99394dded648e4f0c009a71ca237ad6599b8c6abfa250cd186a9c3b794176fa"
CORPUS_DIGEST: Final = "73eaf281286ea16c10b187a4d9548efd58540729c2f693ac43d9605bc5e3a4fa"
_POINTER_TO_ATTRIBUTE: Final = {
    "/idempotencyKey": "idempotency_key",
    "/subjectId": "subject_id",
    "/eventType": "event_type",
    "/actor": "actor",
}
_CAMEL_TO_ATTRIBUTE: Final = {
    "idempotencyKey": "idempotency_key",
    "subjectId": "subject_id",
    "eventType": "event_type",
    "actor": "actor",
}


def audit_persistence_byte_cases(
    repo_root: Path,
    *,
    corpus_path: Path | None = None,
) -> dict[str, object]:
    path = corpus_path if corpus_path is not None else repo_root / CORPUS_PATH
    corpus = _load_json(path)
    _assert_corpus(corpus)
    raw_cases = _require_list(corpus, "cases")
    cases = {
        case_id: _capture(case)
        for raw_case in raw_cases
        for case in [_require_mapping(raw_case, "corpus case")]
        for case_id in [_require_str(case, "id")]
    }
    return {
        "schemaVersion": corpus["schemaVersion"],
        "profileId": corpus["profileId"],
        "profileDigest": corpus["profileDigest"],
        "corpusDigest": CORPUS_DIGEST,
        "limits": corpus["limits"],
        "caseCount": len(raw_cases),
        "cases": cases,
    }


def _assert_corpus(corpus: dict[str, Any]) -> None:
    if _sha256(_canonical_contract_bytes(corpus)) != CORPUS_DIGEST:
        raise ValueError("audit persistence byte corpus has an unadmitted fingerprint")
    if (
        corpus.get("schemaVersion") != CORPUS_SCHEMA
        or corpus.get("profileId") != AUDIT_PERSISTENCE_BYTE_PROFILE_ID
        or corpus.get("profileDigest") != PROFILE_DIGEST
    ):
        raise ValueError("audit persistence byte corpus identity is unsupported")
    expected_limits = {
        "maxVariableTextUtf8Bytes": MAX_AUDIT_TEXT_UTF8_BYTES_V1,
        "maxPayloadCanonicalBytes": MAX_AUDIT_PAYLOAD_CANONICAL_BYTES_V1,
    }
    if corpus.get("limits") != expected_limits:
        raise ValueError("audit persistence byte corpus limits do not match Python policy")
    raw_cases = _require_list(corpus, "cases")
    case_ids = [_require_str(_require_mapping(case, "corpus case"), "id") for case in raw_cases]
    if not case_ids or len(set(case_ids)) != len(case_ids):
        raise ValueError("audit persistence byte corpus case ids must be non-empty and unique")


def _capture(case: dict[str, Any]) -> dict[str, object]:
    try:
        target = _require_str(case, "target")
        if target == "text":
            pointer = _require_str(case, "instancePointer")
            value = _materialize_text(_require_mapping(case.get("recipe"), "text recipe"))
            event_input = _base_input(None)
            attribute = _POINTER_TO_ATTRIBUTE.get(pointer)
            if attribute is None:
                raise ValueError(f"unsupported text instance pointer {pointer}")
            object.__setattr__(event_input, attribute, value)
            admit_audit_text_bytes(event_input)
            return {"accepted": True, "utf8Bytes": len(value.encode("utf8"))}
        if target == "payload":
            payload = _materialize_payload(_require_mapping(case.get("recipe"), "payload recipe"))
            canonical = canonical_audit_payload_bytes(payload)
            return {
                "accepted": True,
                "canonicalByteLength": len(canonical),
                "canonicalSha256": _sha256(canonical),
            }
        if target == "input":
            record = build_audit_event(_materialize_input(case), None)
            return {"accepted": True, "payloadHash": record.payload_hash}
        raise ValueError(f"unsupported audit persistence byte target {target}")
    except AuditEventError as error:
        if error.resource_failure is not None:
            return {
                "accepted": False,
                "resourceFailure": error.resource_failure.to_mapping(),
            }
        return {"accepted": False, "unexpectedError": type(error).__name__}


def _materialize_input(case: dict[str, Any]) -> AuditEventInput:
    payload_recipe = _require_mapping(case.get("payloadRecipe"), "payloadRecipe")
    values: dict[str, object] = {
        "idempotency_key": "audit-persistence-byte-case",
        "subject_id": "byte-profile",
        "event_type": "byte-profile.checked",
        "actor": "ci-coordinator",
    }
    text_recipe = case.get("textRecipe")
    if text_recipe is not None:
        text = _materialize_text(_require_mapping(text_recipe, "textRecipe"))
        values = {key: text for key in values}
    overrides = case.get("textOverrides", {})
    for field, raw_recipe in _require_mapping(overrides, "textOverrides").items():
        attribute = _CAMEL_TO_ATTRIBUTE.get(field)
        if attribute is None:
            raise ValueError(f"unsupported text override {field}")
        values[attribute] = _materialize_text(_require_mapping(raw_recipe, f"{field} recipe"))
    return AuditEventInput(
        idempotency_key=cast(str, values["idempotency_key"]),
        subject_type="dynamic-ci-plan",
        subject_id=cast(str, values["subject_id"]),
        event_type=cast(str, values["event_type"]),
        created_at="2026-07-11T00:00:00.000Z",
        actor=cast(str, values["actor"]),
        payload=cast(JsonValue, _materialize_payload(payload_recipe)),
    )


def _base_input(payload: object) -> AuditEventInput:
    return AuditEventInput(
        idempotency_key="audit-persistence-byte-case",
        subject_type="dynamic-ci-plan",
        subject_id="byte-profile",
        event_type="byte-profile.checked",
        created_at="2026-07-11T00:00:00.000Z",
        actor="ci-coordinator",
        payload=cast(Any, payload),
    )


def _materialize_payload(recipe: dict[str, Any]) -> object:
    kind = _require_str(recipe, "kind")
    if kind == "literal":
        return recipe.get("value")
    if kind == "json-string":
        return _code_point(_require_int(recipe, "codePoint")) * _require_int(recipe, "count")
    if kind == "json-string-sequence":
        return _materialize_sequence(recipe)
    if kind == "object-key":
        key = _code_point(_require_int(recipe, "codePoint")) * _require_int(recipe, "count")
        return {key: recipe.get("value")}
    if kind == "object-key-sequence":
        return {_materialize_sequence(recipe): recipe.get("value")}
    raise ValueError(f"recipe {kind} is not a payload recipe")


def _materialize_text(recipe: dict[str, Any]) -> str:
    kind = _require_str(recipe, "kind")
    if kind == "repeat-code-point":
        return _code_point(_require_int(recipe, "codePoint")) * _require_int(recipe, "count")
    if kind == "repeat-sequence":
        return _materialize_sequence(recipe)
    raise ValueError(f"recipe {kind} is not a text recipe")


def _materialize_sequence(recipe: dict[str, Any]) -> str:
    code_points = _require_int_list(recipe, "codePoints")
    suffix = _require_int_list(recipe, "suffixCodePoints")
    return "".join(map(_code_point, code_points)) * _require_int(recipe, "count") + "".join(
        map(_code_point, suffix)
    )


def _code_point(value: int) -> str:
    if value < 0 or value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
        raise ValueError("recipe codePoint must be a Unicode scalar value")
    return chr(value)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf8"))
    return _require_mapping(value, str(path))


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _require_list(value: dict[str, Any], key: str) -> list[object]:
    result = value.get(key)
    if not isinstance(result, list):
        raise TypeError(f"{key} must be an array")
    return cast(list[object], result)


def _require_str(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result


def _require_int(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if type(result) is not int or result < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return result


def _require_int_list(value: dict[str, Any], key: str) -> list[int]:
    result = value.get(key)
    if not isinstance(result, list) or any(type(item) is not int for item in result):
        raise ValueError(f"{key} must be an integer array")
    return cast(list[int], result)


def _canonical_contract_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    root = Path(__file__).resolve().parents[2]
    print(
        json.dumps(
            audit_persistence_byte_cases(root, corpus_path=arguments.corpus),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
