from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from scripts.config_lifecycle_http_profile import load_config_lifecycle_http_profile
from scripts.control_plane_profile import (
    DEFAULT_PROFILE_PATH,
    REPO_ROOT,
    load_control_plane_profile,
    profile_report,
)
from scripts.tests.profile_mutation_support import MutationPath, profile_leaves
from scripts.tests.profile_mutation_support import replace_profile_value as _replace


def test_repository_control_plane_profile_is_admitted() -> None:
    profile = load_control_plane_profile()

    assert profile.credential_plane_ids == (
        "emergency-safety",
        "human-administration",
        "identity-provider-capability",
        "machine-administration",
        "provider-capability",
        "provider-event",
        "repository-attestation",
        "target-workflow",
    )
    assert len(profile.credential_kinds) == 14
    assert len(profile.operation_ids) == 12
    lifecycle_operations = {
        operation.semantic_operation_id
        for operation in load_config_lifecycle_http_profile().operations
    }
    assert lifecycle_operations <= set(profile.operation_ids)
    assert profile_report(profile)["state"] == "passed"


def test_lifecycle_profile_is_an_exact_control_plane_authority_projection() -> None:
    lifecycle = load_config_lifecycle_http_profile()
    control_operations = {
        cast(str, operation["operationId"]): operation
        for operation in cast(list[dict[str, object]], _profile_document()["operations"])
    }

    for operation in lifecycle.operations:
        authority = control_operations[operation.semantic_operation_id]
        callers = cast(list[dict[str, object]], authority["callerAuthorities"])
        assert {cast(str, caller["credentialPlane"]) for caller in callers} == {
            "human-administration",
            "machine-administration",
        }
        assert {tuple(cast(list[str], caller["requiredRoles"])) for caller in callers} == {
            operation.required_roles
        }
        provider_effect = cast(dict[str, object], authority["providerEffect"])
        assert (provider_effect["mode"] == "required") == ("provider" in operation.effect)


@pytest.mark.parametrize(
    ("path", "replacement", "diagnostic"),
    (
        (
            ("credentialPlanes", 1, "credentialKinds"),
            ["deployment-break-glass-bearer"],
            "credential kinds overlap",
        ),
        (
            ("credentialPlanes", 1, "credentialKinds"),
            ["different-human-credential"],
            "credential plane projection differs",
        ),
        (
            ("operations", 3, "callerAuthorities"),
            [
                {"credentialPlane": "emergency-safety", "requiredRoles": []},
                {
                    "credentialPlane": "human-administration",
                    "requiredRoles": ["activate", "override"],
                },
                {
                    "credentialPlane": "machine-administration",
                    "requiredRoles": ["activate", "override"],
                },
            ],
            "operation authority differs",
        ),
        (
            ("operations", 3, "callerAuthorities", 0, "requiredRoles"),
            ["activate"],
            "operation authority differs",
        ),
        (
            ("operations", 4, "callerAuthorities", 0, "requiredRoles"),
            ["override"],
            "operation authority differs",
        ),
        (
            ("operations", 0, "providerEffect", "mode"),
            "forbidden",
            "operation authority differs",
        ),
        (
            ("operations", 11, "providerEvidenceInput"),
            None,
            "operation authority differs",
        ),
        (
            ("breakGlass", "productionActions"),
            ["disable-omission", "enable-omission", "force-full-ci"],
            "break-glass actions differ",
        ),
        (
            ("keycloak", "browser", "sessionMaximumSeconds"),
            901,
            "Keycloak profile differs",
        ),
        (
            ("keycloak", "browser", "retainAccessToken"),
            True,
            "Keycloak profile differs",
        ),
        (
            ("keycloak", "browser", "clientAuthentication", "custodyOwner"),
            "application",
            "Keycloak profile differs",
        ),
        (
            ("keycloak", "backChannelLogout", "rejectNonce"),
            False,
            "Keycloak profile differs",
        ),
        (
            ("keycloak", "backChannelLogout", "requireExpiration"),
            False,
            "Keycloak profile differs",
        ),
        (
            ("githubApp", "currentPermissionProfile", "permissions", "actions"),
            "write",
            "GitHub App profile differs",
        ),
        (
            (
                "githubApp",
                "currentPermissionProfile",
                "permissions",
                "organization_self_hosted_runners",
            ),
            "write",
            "GitHub App profile differs",
        ),
        (
            ("githubApp", "futurePermissionProfiles", 0, "status"),
            "active",
            "GitHub App profile differs",
        ),
        (
            ("githubApp", "targetApplication", "appId"),
            4450941,
            "GitHub App profile differs",
        ),
        (
            ("githubApp", "targetApplication", "public"),
            False,
            "GitHub App profile differs",
        ),
        (
            ("repositoryAttestation", "independentFromKeycloak"),
            False,
            "repository attestation differs",
        ),
        (
            ("repositoryAttestation", "stepUp", "retainUserAccessToken"),
            True,
            "repository attestation differs",
        ),
        (
            ("githubApp", "reviewerStepUpOAuth", "clientSecretCustodyOwner"),
            "application",
            "GitHub App profile differs",
        ),
        (
            ("githubApp", "appAuthentication", "privateKeyCustodyOwner"),
            "application",
            "GitHub App profile differs",
        ),
        (
            ("routes", "repositoryAttestationCallback"),
            "/api/v1/auth/github/callback",
            "control-plane routes differ",
        ),
        (
            ("attribution", "providerEffectsRetainInitiatingActor"),
            False,
            "actor attribution differs",
        ),
        (
            ("supportBoundary", "releaseState"),
            "released",
            "control-plane support boundary differs",
        ),
        (
            ("liveDevelopmentObservations", "authorityState"),
            "production-ready",
            "development provider observations differ",
        ),
        (
            ("nonClaims", 0),
            "Complete administrator route mapping remains deferred.",
            "control-plane non-claims differ",
        ),
    ),
)
def test_profile_rejects_single_field_authority_mutations(
    tmp_path: Path,
    path: MutationPath,
    replacement: object,
    diagnostic: str,
) -> None:
    document = _profile_document()
    _replace(document, path, replacement)
    _write_profile(tmp_path, document)

    with pytest.raises(ValueError, match=diagnostic):
        load_control_plane_profile(tmp_path, Path("profile.json"))


def test_profile_rejects_every_single_leaf_mutation(tmp_path: Path) -> None:
    document = _profile_document()
    mutations = _single_leaf_mutations(document)
    assert mutations

    for path, replacement in mutations:
        candidate = copy.deepcopy(document)
        _replace(candidate, path, replacement)
        _write_profile(tmp_path, candidate)
        try:
            load_control_plane_profile(tmp_path, Path("profile.json"))
        except ValueError:
            continue
        pytest.fail(f"profile admitted a single-leaf mutation at {path!r}")


def test_profile_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    source = (REPO_ROOT / DEFAULT_PROFILE_PATH).read_text(encoding="utf-8")
    duplicate = source.replace(
        '  "schemaVersion": 1,',
        '  "schemaVersion": 1,\n  "schemaVersion": 1,',
        1,
    )
    (tmp_path / "profile.json").write_text(duplicate, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate key: schemaVersion"):
        load_control_plane_profile(tmp_path, Path("profile.json"))


def _profile_document() -> dict[str, object]:
    loaded = json.loads((REPO_ROOT / DEFAULT_PROFILE_PATH).read_text(encoding="utf-8"))
    return copy.deepcopy(cast(dict[str, object], loaded))


def _write_profile(root: Path, document: dict[str, object]) -> None:
    (root / "profile.json").write_text(
        json.dumps(document, ensure_ascii=True, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _single_leaf_mutations(
    document: dict[str, object],
) -> list[tuple[MutationPath, object]]:
    mutations: list[tuple[MutationPath, object]] = []
    for path, value in profile_leaves(document):
        if isinstance(value, list):
            mutations.append((path, ["unexpected-value"]))
            continue
        if isinstance(value, bool):
            replacements: tuple[object, ...] = (not value, int(value))
        elif isinstance(value, int):
            replacements = (value + 1, float(value))
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
        (("nested", 0, "flag"), int, 1),
        (("nested", 1), int, 3),
        (("nested", 1), float, 2.0),
        (("empty",), list, ["unexpected-value"]),
    ]
