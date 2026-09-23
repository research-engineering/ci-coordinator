from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from typing import cast

import pytest

from ci_coordinator.ci_economics import (
    CI_ECONOMICS_PROFILE_SHA256,
    CiEconomicsHttpOperation,
    CollectionPolicy,
    load_bundled_ci_economics_profile,
    parse_ci_economics_profile,
)
from ci_coordinator.ci_economics import profile as profile_module
from ci_coordinator.ci_economics.profile import CI_ECONOMICS_PROFILE_RESOURCE

type _InvalidProfileFactory = Callable[[], object]
type _HttpOperationMutation = Callable[[CiEconomicsHttpOperation], CiEconomicsHttpOperation]


def test_bundled_profile_is_digest_pinned_and_round_trips() -> None:
    raw = (
        files("ci_coordinator.ci_economics.resources")
        .joinpath(CI_ECONOMICS_PROFILE_RESOURCE)
        .read_bytes()
    )

    profile = load_bundled_ci_economics_profile()

    assert sha256(raw).hexdigest() == CI_ECONOMICS_PROFILE_SHA256
    assert profile.source_digest == CI_ECONOMICS_PROFILE_SHA256
    assert parse_ci_economics_profile(raw) == profile


@pytest.mark.parametrize(
    ("section_name", "field_name", "replacement"),
    [
        ("collection", "windowSeconds", 604_801),
        ("retention", "evidenceDays", 91),
        ("maintenance", "collectionDeadlineSeconds", 241),
        ("http", "attempts", {}),
        ("bounds", "maximumJobsPerAttempt", 2_001),
    ],
)
def test_v1_profile_rejects_semantic_substitution(
    section_name: str,
    field_name: str,
    replacement: object,
) -> None:
    raw = (
        files("ci_coordinator.ci_economics.resources")
        .joinpath(CI_ECONOMICS_PROFILE_RESOURCE)
        .read_bytes()
    )
    document = json.loads(raw)
    section = document[section_name]
    assert type(section) is dict
    section[field_name] = replacement

    with pytest.raises(ValueError):
        parse_ci_economics_profile(json.dumps(document).encode("utf-8"))


def test_profile_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="duplicate-free"):
        parse_ci_economics_profile(b'{"schemaVersion":"a","schemaVersion":"b"}')


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda operation: replace(operation, operation_key="other"), "key is invalid"),
        (lambda operation: replace(operation, method="POST"), "route identity"),
        (lambda operation: replace(operation, path="/other"), "route identity"),
        (lambda operation: replace(operation, operation_id=""), "authority"),
        (lambda operation: replace(operation, required_role="admin"), "authority"),
        (
            lambda operation: replace(operation, identity_parameter="other"),
            "identity parameter",
        ),
        (
            lambda operation: replace(operation, cursor_parameter="other"),
            "cursor parameter",
        ),
        (lambda operation: replace(operation, default_page_size=0), "page bounds"),
        (lambda operation: replace(operation, maximum_page_size=99), "page bounds"),
    ],
)
def test_http_operation_rejects_mutations_of_its_closed_contract(
    mutate: _HttpOperationMutation,
    message: str,
) -> None:
    operation = load_bundled_ci_economics_profile().http_operations[0]

    with pytest.raises(ValueError, match=message):
        mutate(operation)


@pytest.mark.parametrize(
    ("factory", "error", "message"),
    [
        (
            lambda: replace(load_bundled_ci_economics_profile(), source_digest="A" * 64),
            ValueError,
            "digest is invalid",
        ),
        (
            lambda: replace(load_bundled_ci_economics_profile(), cleanup_batch_size=0),
            ValueError,
            "positive integer",
        ),
        (
            lambda: replace(
                load_bundled_ci_economics_profile(),
                collection_policy=cast(CollectionPolicy, object()),
            ),
            TypeError,
            "exact collection policy",
        ),
        (
            lambda: replace(
                load_bundled_ci_economics_profile(),
                http_operations=cast(
                    tuple[CiEconomicsHttpOperation, ...],
                    list(load_bundled_ci_economics_profile().http_operations),
                ),
            ),
            ValueError,
            "incomplete or unordered",
        ),
        (
            lambda: replace(
                load_bundled_ci_economics_profile(),
                http_operations=tuple(
                    reversed(load_bundled_ci_economics_profile().http_operations)
                ),
            ),
            ValueError,
            "incomplete or unordered",
        ),
        (
            lambda: replace(
                load_bundled_ci_economics_profile(),
                maximum_concurrent_claims=(
                    load_bundled_ci_economics_profile().maximum_claims_per_round + 1
                ),
            ),
            ValueError,
            "concurrency cannot exceed",
        ),
    ],
)
def test_profile_value_rejects_states_outside_its_resource_contract(
    factory: _InvalidProfileFactory,
    error: type[BaseException],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        factory()


@pytest.mark.parametrize(
    ("field_name", "replacement", "message"),
    [
        ("schemaVersion", "ci-economics-profile/v2", "identity is invalid"),
        ("persistedWebhookEvents", [], "persisted webhook events"),
        ("accuracyClasses", ["exact"], "accuracy classes"),
        ("forbiddenClaims", [], "forbidden claims"),
    ],
)
def test_profile_parser_rejects_top_level_semantic_substitution(
    field_name: str,
    replacement: object,
    message: str,
) -> None:
    document = _profile_document()
    document[field_name] = replacement

    with pytest.raises(ValueError, match=message):
        parse_ci_economics_profile(json.dumps(document).encode())


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"\xff", "duplicate-free UTF-8 JSON"),
        (b"[]", "must be an object"),
    ],
)
def test_profile_parser_rejects_non_document_inputs(raw: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_ci_economics_profile(raw)


def test_profile_parser_rejects_non_mapping_and_non_exact_sections() -> None:
    non_mapping = _profile_document()
    non_mapping["collection"] = []
    missing_key = _profile_document()
    cast(dict[str, object], missing_key["bounds"]).pop("maximumJobLabels")

    with pytest.raises(ValueError, match="collection must be an object"):
        parse_ci_economics_profile(json.dumps(non_mapping).encode())
    with pytest.raises(ValueError, match="bounds keys are not exact"):
        parse_ci_economics_profile(json.dumps(missing_key).encode())


def test_bundled_profile_rejects_bytes_outside_the_pinned_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profile_module, "CI_ECONOMICS_PROFILE_SHA256", "0" * 64)

    with pytest.raises(ValueError, match="digest does not match"):
        load_bundled_ci_economics_profile()


def _profile_document() -> dict[str, object]:
    raw = (
        files("ci_coordinator.ci_economics.resources")
        .joinpath(CI_ECONOMICS_PROFILE_RESOURCE)
        .read_bytes()
    )
    return cast(dict[str, object], json.loads(raw))
