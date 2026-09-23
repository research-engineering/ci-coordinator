from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from scripts.config_lifecycle_http_profile import (
    DEFAULT_PROFILE_PATH,
    REPO_ROOT,
    assert_config_lifecycle_openapi,
    load_config_lifecycle_http_profile,
)
from scripts.frontend_contract import rendered_contract
from scripts.tests.profile_mutation_support import MutationPath, profile_leaves
from scripts.tests.profile_mutation_support import replace_profile_value as _replace


def test_repository_profile_is_exact_and_matches_openapi() -> None:
    profile = load_config_lifecycle_http_profile()
    document = cast(dict[str, object], json.loads(rendered_contract()))

    assert profile.maximum_contract_identity_bytes == 4_096
    assert profile.maximum_json_safe_integer == 9_007_199_254_740_991
    assert profile.maximum_page_size == 100
    assert profile.maximum_policy_source_bytes == 2_097_152
    assert profile.maximum_operation_id_bytes == 256
    assert profile.maximum_rollback_reason_bytes == 512
    assert len(profile.operations) == 6
    assert_config_lifecycle_openapi(document, profile)


def test_profile_rejects_every_single_leaf_mutation(tmp_path: Path) -> None:
    document = _profile_document()
    mutations = _single_leaf_mutations(document)
    assert mutations

    for path, replacement in mutations:
        candidate = copy.deepcopy(document)
        _replace(candidate, path, replacement)
        _write_profile(tmp_path, candidate)
        with pytest.raises((TypeError, ValueError)):
            load_config_lifecycle_http_profile(tmp_path, Path("profile.json"))


def test_profile_rejects_each_deleted_operation_and_extra_field(tmp_path: Path) -> None:
    document = _profile_document()
    operations = cast(list[object], document["operations"])
    for index in range(len(operations)):
        candidate = copy.deepcopy(document)
        del cast(list[object], candidate["operations"])[index]
        _write_profile(tmp_path, candidate)
        with pytest.raises(ValueError, match="operations differ"):
            load_config_lifecycle_http_profile(tmp_path, Path("profile.json"))

    document["unexpected"] = True
    _write_profile(tmp_path, document)
    with pytest.raises(ValueError, match="fields differ"):
        load_config_lifecycle_http_profile(tmp_path, Path("profile.json"))


def test_profile_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    source = (REPO_ROOT / DEFAULT_PROFILE_PATH).read_text(encoding="utf-8")
    duplicate = source.replace(
        '  "profileId": "ci-config-lifecycle-http/v1",',
        (
            '  "profileId": "ci-config-lifecycle-http/v1",\n'
            '  "profileId": "ci-config-lifecycle-http/v1",'
        ),
        1,
    )
    (tmp_path / "profile.json").write_text(duplicate, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate key: profileId"):
        load_config_lifecycle_http_profile(tmp_path, Path("profile.json"))


@pytest.mark.parametrize(
    ("path", "replacement", "diagnostic"),
    (
        (
            ("paths", "/api/v1/config/validations", "post", "operationId"),
            "changed_operation",
            "operation identity differs",
        ),
        (
            (
                "components",
                "schemas",
                "ConfigEpochRegistrationBody",
                "properties",
                "schemaVersion",
                "const",
            ),
            "ci-config-epoch-registration/v2",
            "request schema differs",
        ),
        (
            (
                "components",
                "schemas",
                "ConfigEpochAcceptedBody",
                "properties",
                "schemaVersion",
                "const",
            ),
            "ci-config-epoch-registration-result/v2",
            "success schema differs",
        ),
        (
            (
                "paths",
                "/api/v1/config/repositories/{installation_id}/{repository_id}/status",
                "get",
                "parameters",
                3,
                "schema",
                "maximum",
            ),
            101,
            "page bound differs",
        ),
        (
            (
                "components",
                "schemas",
                "ConfigEpochValidationBody",
                "properties",
                "source",
                "x-max-utf8-bytes",
            ),
            2_097_153,
            "policy source bound differs",
        ),
        (
            (
                "components",
                "schemas",
                "ConfigEpochActivationBody",
                "properties",
                "operationId",
                "x-max-utf8-bytes",
            ),
            257,
            "operation identity bound differs",
        ),
        (
            (
                "components",
                "schemas",
                "ConfigEpochRollbackBody",
                "properties",
                "reason",
                "x-max-utf8-bytes",
            ),
            513,
            "rollback reason bound differs",
        ),
        (
            (
                "components",
                "schemas",
                "ConfigEpochStatusBody",
                "properties",
                "epochs",
                "maxItems",
            ),
            101,
            "status response bound differs",
        ),
        (
            (
                "components",
                "schemas",
                "PolicySourceByteCount",
                "maximum",
            ),
            2_097_153,
            "source byte-count bound differs",
        ),
        (
            (
                "components",
                "schemas",
                "ConfigContractResourceId",
                "x-max-utf8-bytes",
            ),
            4_097,
            "contract identity bound differs",
        ),
        (
            (
                "components",
                "schemas",
                "LowercaseSha256Hex",
                "pattern",
            ),
            "^[0-9a-f]+$",
            "SHA-256 contract differs",
        ),
        (
            (
                "components",
                "schemas",
                "JsonSafePositiveInteger",
                "maximum",
            ),
            9_007_199_254_740_992,
            "safe-integer bound differs",
        ),
    ),
)
def test_openapi_projection_rejects_authority_drift(
    path: MutationPath,
    replacement: object,
    diagnostic: str,
) -> None:
    document = cast(dict[str, object], json.loads(rendered_contract()))
    _replace(document, path, replacement)

    with pytest.raises(ValueError, match=diagnostic):
        assert_config_lifecycle_openapi(document, load_config_lifecycle_http_profile())


def test_openapi_projection_rejects_deleted_and_extra_config_routes() -> None:
    profile = load_config_lifecycle_http_profile()
    original = cast(dict[str, object], json.loads(rendered_contract()))
    paths = cast(dict[str, object], original["paths"])

    deleted = copy.deepcopy(original)
    del cast(dict[str, object], deleted["paths"])["/api/v1/config/validations"]
    with pytest.raises(ValueError, match="route set differs"):
        assert_config_lifecycle_openapi(deleted, profile)

    paths["/api/v1/config/unadmitted"] = {"get": {}}
    with pytest.raises(ValueError, match="route set differs"):
        assert_config_lifecycle_openapi(original, profile)


def _profile_document() -> dict[str, object]:
    loaded = json.loads((REPO_ROOT / DEFAULT_PROFILE_PATH).read_text(encoding="utf-8"))
    return copy.deepcopy(cast(dict[str, object], loaded))


def _write_profile(root: Path, document: dict[str, object]) -> None:
    (root / "profile.json").write_text(
        json.dumps(document, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _single_leaf_mutations(
    document: dict[str, object],
) -> list[tuple[MutationPath, object]]:
    mutations: list[tuple[MutationPath, object]] = []
    for path, value in profile_leaves(document):
        if isinstance(value, list):
            continue
        if isinstance(value, bool):
            replacements: tuple[object, ...] = (not value,)
        elif isinstance(value, int):
            replacements = (value + 1,)
        elif isinstance(value, str):
            replacements = (f"{value}-mutated",)
        elif value is None:
            replacements = ("unexpected-value",)
        else:
            raise AssertionError(f"unsupported profile leaf at {path!r}")
        mutations.extend((path, replacement) for replacement in replacements)
    return mutations


def test_leaf_mutation_policy_preserves_order_types_and_empty_arrays() -> None:
    mutations = _single_leaf_mutations({"nested": [{"flag": True}, 2], "empty": []})
    assert [(path, type(value), value) for path, value in mutations] == [
        (("nested", 0, "flag"), bool, False),
        (("nested", 1), int, 3),
    ]
