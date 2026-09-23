"""Canonical target-repository trust root for signed execution plans."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_public_key

from ci_coordinator.kernel import canonical_json
from ci_coordinator.target_artifacts.schemas import validate_artifact_value
from ci_coordinator.target_artifacts.workflow import check_exact_file, write_exact_file

PLAN_TRUST_ROOT_FILENAME: Final = "plan-trust-root.v1.json"
PLAN_TRUST_ROOT_SCHEMA: Final = "ci-coordinator-plan-trust-root/v1"
PLAN_TRUST_ROOT_ALGORITHM: Final = "Ed25519"
_KEY_ID: Final = re.compile(r"[A-Za-z0-9._-]{1,128}")


def render_plan_trust_root(*, key_id: str, public_key_pem: bytes) -> bytes:
    """Render the exact trust root admitted by the target-side verifier."""
    if type(key_id) is not str or _KEY_ID.fullmatch(key_id) is None:
        raise ValueError("plan trust-root key id is invalid")
    if type(public_key_pem) is not bytes or not 1 <= len(public_key_pem) <= 16_384:
        raise ValueError("plan trust-root public key is invalid")
    public_key = load_pem_public_key(public_key_pem)
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("plan trust root requires an Ed25519 public key")
    value = {
        "algorithm": PLAN_TRUST_ROOT_ALGORITHM,
        "keyId": key_id,
        "publicKeySpkiBase64": base64.b64encode(
            public_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).decode("ascii"),
        "schemaVersion": PLAN_TRUST_ROOT_SCHEMA,
    }
    validate_artifact_value("plan_trust_root", value)
    return canonical_json(value) + b"\n"


def check_plan_trust_root(path: Path, expected: bytes) -> bool:
    return check_exact_file(path, expected)


def write_plan_trust_root(path: Path, content: bytes) -> None:
    write_exact_file(path, content)
