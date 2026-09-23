from __future__ import annotations

import json
from base64 import b64decode, b64encode
from importlib.metadata import version
from typing import Any

from schemathesis import Case
from schemathesis.core import NOT_SET
from schemathesis.generation.meta import CaseMetadata

from .harness import Harness
from .profile import HEADERS, OPERATIONS


def encode_case(case: Case[Any], harness: Harness) -> bytes:
    metadata = case.meta
    return json.dumps(
        {
            "schema_sha256": harness.schema_digest,
            "profile": harness.campaign.name,
            "seed": harness.campaign.seed,
            "schemathesis": version("schemathesis"),
            "hypothesis": version("hypothesis"),
            "operation": case.operation.label,
            "phase": metadata.phase.name.value if metadata else "explicit",
            "method": case.method,
            "path": case.path,
            "path_parameters": case.path_parameters,
            "query": case.query,
            "headers": dict(case.headers),
            "cookies": case.cookies,
            "body_present": case.body is not NOT_SET,
            "body": (
                b64encode(case.body).decode("ascii")
                if isinstance(case.body, bytes)
                else case.body
                if case.body is not NOT_SET
                else None
            ),
            "body_encoding": "base64" if isinstance(case.body, bytes) else "json",
            "media_type": case.media_type,
            "metadata": metadata.to_dict() if metadata else None,
            "transport_headers": dict(HEADERS),
        },
        ensure_ascii=True,
    ).encode("utf-8")


def decode_case(record: bytes, harness: Harness) -> Case[Any]:
    data = json.loads(record)
    if not isinstance(data, dict):
        raise ValueError("Exact case must be a JSON object")
    for field, expected in {
        "schema_sha256": harness.schema_digest,
        "profile": harness.campaign.name,
        "seed": harness.campaign.seed,
        "schemathesis": version("schemathesis"),
        "hypothesis": version("hypothesis"),
        "transport_headers": dict(HEADERS),
    }.items():
        if data.get(field) != expected:
            raise ValueError(f"Exact case {field} does not match the harness")
    method, path = data["method"], data["path"]
    if (method, path) not in OPERATIONS:
        raise ValueError("Exact case operation is outside the admitted manifest")
    operation = harness.schema[path][method]
    if data["operation"] != operation.label:
        raise ValueError("Exact case operation label does not match the manifest")
    metadata = CaseMetadata.from_dict(data["metadata"]) if data["metadata"] is not None else None
    if data["phase"] != (metadata.phase.name.value if metadata else "explicit"):
        raise ValueError("Exact case phase does not match its metadata")
    present, encoding, body = data["body_present"], data["body_encoding"], data["body"]
    if type(present) is not bool or encoding not in ("json", "base64"):
        raise ValueError("Exact case body presence or encoding is invalid")
    if not present:
        if body is not None or encoding != "json":
            raise ValueError("Exact case absent body has a payload or binary encoding")
        body = NOT_SET
    elif encoding == "base64":
        if not isinstance(body, str):
            raise ValueError("Exact case binary body must be a base64 string")
        body = b64decode(body, validate=True)
    return operation.Case(
        method=method,
        path_parameters=data["path_parameters"],
        query=data["query"],
        headers=data["headers"],
        cookies=data["cookies"],
        body=body,
        media_type=data["media_type"],
        _meta=metadata,
    )
