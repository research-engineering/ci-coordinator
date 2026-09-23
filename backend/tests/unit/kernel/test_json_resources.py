from __future__ import annotations

import pytest

from ci_coordinator.kernel import (
    CanonicalJsonError,
    JsonResourceLimits,
    StrictJsonError,
    canonical_json,
    load_strict_json,
    try_canonical_json,
)
from ci_coordinator.kernel.canonical_json import canonical_json_text

LIMITS = JsonResourceLimits(max_depth=64, max_nodes=10_000)


@pytest.mark.parametrize(
    ("content", "limits"),
    (
        (b"[[null]]", JsonResourceLimits(max_depth=1, max_nodes=10)),
        (b"[null,null]", JsonResourceLimits(max_depth=1, max_nodes=2)),
    ),
)
def test_strict_json_rejects_structural_overflow_before_materialization(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    limits: JsonResourceLimits,
) -> None:
    materialized = False

    def fail_if_materialized(*_args: object, **_kwargs: object) -> object:
        nonlocal materialized
        materialized = True
        raise AssertionError("overflowing JSON must not be materialized")

    monkeypatch.setattr("ci_coordinator.kernel.strict_json.json.loads", fail_if_materialized)

    with pytest.raises(StrictJsonError):
        load_strict_json(content, max_bytes=len(content), resource_limits=limits)

    assert materialized is False


def test_strict_json_resource_preflight_matches_canonical_node_and_depth_semantics() -> None:
    content = b'{"items":[null]}'

    assert load_strict_json(
        content,
        max_bytes=len(content),
        resource_limits=JsonResourceLimits(max_depth=2, max_nodes=3),
    ) == {"items": [None]}


@pytest.mark.parametrize(
    ("max_depth", "max_nodes"),
    [(-1, 1), (0, 0), (True, 1), (0, True)],
)
def test_resource_limit_profile_rejects_invalid_bounds(
    max_depth: object,
    max_nodes: object,
) -> None:
    with pytest.raises(ValueError):
        JsonResourceLimits(
            max_depth=max_depth,  # type: ignore[arg-type]
            max_nodes=max_nodes,  # type: ignore[arg-type]
        )


def test_scalar_and_empty_container_roots_are_one_node_at_depth_zero() -> None:
    assert canonical_json(None, resource_limits=LIMITS) == b"null"
    assert canonical_json_text(None, resource_limits=LIMITS) == "null"
    assert canonical_json([], resource_limits=LIMITS) == b"[]"
    assert canonical_json({}, resource_limits=LIMITS) == b"{}"


@pytest.mark.parametrize("empty_value", [[], {}])
def test_nested_empty_containers_consume_node_budget(empty_value: object) -> None:
    limits = JsonResourceLimits(max_depth=2, max_nodes=4)

    with pytest.raises(CanonicalJsonError) as failure:
        canonical_json([[empty_value], [empty_value]], resource_limits=limits)

    assert_resource_error(
        failure.value,
        code="json_max_nodes_exceeded",
        instance_pointer="/1",
        limit=limits.max_nodes,
        observed=limits.max_nodes + 1,
    )


def test_exact_depth_boundary_and_hostile_depth_use_domain_error() -> None:
    canonical_json(nested_array(LIMITS.max_depth), resource_limits=LIMITS)

    with pytest.raises(CanonicalJsonError) as boundary:
        canonical_json(nested_array(LIMITS.max_depth + 1), resource_limits=LIMITS)
    assert_resource_error(
        boundary.value,
        code="json_max_depth_exceeded",
        instance_pointer="/0" * (LIMITS.max_depth + 1),
        limit=LIMITS.max_depth,
        observed=LIMITS.max_depth + 1,
    )

    with pytest.raises(CanonicalJsonError) as hostile:
        canonical_json(nested_array(12_000), resource_limits=LIMITS)
    assert hostile.value.code == "json_max_depth_exceeded"
    assert hostile.value.instance_pointer == "/0" * (LIMITS.max_depth + 1)


def test_parameterized_depth_above_host_stack_serializes_iteratively() -> None:
    depth = 1_200
    limits = JsonResourceLimits(max_depth=2_000, max_nodes=depth + 1)

    assert canonical_json(nested_array(depth), resource_limits=limits) == (
        (b"[" * depth) + b"null" + (b"]" * depth)
    )


def test_exact_node_boundary() -> None:
    canonical_json([None] * (LIMITS.max_nodes - 1), resource_limits=LIMITS)

    with pytest.raises(CanonicalJsonError) as failure:
        canonical_json([None] * LIMITS.max_nodes, resource_limits=LIMITS)
    assert_resource_error(
        failure.value,
        code="json_max_nodes_exceeded",
        instance_pointer="",
        limit=LIMITS.max_nodes,
        observed=LIMITS.max_nodes + 1,
    )
    assert failure.value.path == "$"


def test_each_visit_checks_depth_before_nodes_and_value_validation() -> None:
    with pytest.raises(CanonicalJsonError) as depth_failure:
        canonical_json(
            [object()],
            resource_limits=JsonResourceLimits(max_depth=0, max_nodes=10),
        )
    assert_resource_error(
        depth_failure.value,
        code="json_max_depth_exceeded",
        instance_pointer="/0",
        limit=0,
        observed=1,
    )
    assert depth_failure.value.path == "$[0]"

    with pytest.raises(CanonicalJsonError) as node_failure:
        canonical_json(
            [[None], object()],
            resource_limits=JsonResourceLimits(max_depth=10, max_nodes=3),
        )
    assert_resource_error(
        node_failure.value,
        code="json_max_nodes_exceeded",
        instance_pointer="/1",
        limit=3,
        observed=4,
    )
    assert node_failure.value.path == "$[1]"


@pytest.mark.parametrize("hidden_fault", [object(), [[None]]])
def test_direct_child_lower_bound_precedes_hidden_value_or_depth_fault(
    hidden_fault: object,
) -> None:
    limits = JsonResourceLimits(max_depth=0, max_nodes=1)

    with pytest.raises(CanonicalJsonError) as failure:
        canonical_json([hidden_fault], resource_limits=limits)
    assert_resource_error(
        failure.value,
        code="json_max_nodes_exceeded",
        instance_pointer="",
        limit=limits.max_nodes,
        observed=limits.max_nodes + 1,
    )
    assert failure.value.path == "$"


def test_object_keys_do_not_count_as_nodes() -> None:
    at_limit = {f"k{index}": None for index in range(LIMITS.max_nodes - 1)}
    canonical_json(at_limit, resource_limits=LIMITS)

    with pytest.raises(CanonicalJsonError) as failure:
        canonical_json({**at_limit, "overflow": None}, resource_limits=LIMITS)
    assert_resource_error(
        failure.value,
        code="json_max_nodes_exceeded",
        instance_pointer="",
        limit=LIMITS.max_nodes,
        observed=LIMITS.max_nodes + 1,
    )
    assert failure.value.path == "$"


def test_repeated_aliases_count_per_serialized_occurrence() -> None:
    shared = [None] * 4_999
    canonical_json([shared], resource_limits=LIMITS)

    with pytest.raises(CanonicalJsonError) as failure:
        canonical_json([shared, shared], resource_limits=LIMITS)
    assert_resource_error(
        failure.value,
        code="json_max_nodes_exceeded",
        instance_pointer="/1",
        limit=LIMITS.max_nodes,
        observed=LIMITS.max_nodes + 1,
    )
    assert failure.value.path == "$[1]"


def test_cycles_remain_outside_the_json_tree_domain() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)

    with pytest.raises(CanonicalJsonError) as failure:
        canonical_json(cyclic, resource_limits=LIMITS)
    assert failure.value.code == "cycle"

    cyclic_object: dict[str, object] = {}
    cyclic_object["self"] = cyclic_object
    with pytest.raises(CanonicalJsonError) as object_failure:
        canonical_json(cyclic_object, resource_limits=LIMITS)
    assert object_failure.value.code == "cycle"


def test_object_insertion_order_cannot_change_resource_failure() -> None:
    limits = JsonResourceLimits(max_depth=10, max_nodes=5)
    ascending = {"a": [None, None], "z": [None]}
    descending = dict(reversed(tuple(ascending.items())))

    with pytest.raises(CanonicalJsonError) as left:
        canonical_json(ascending, resource_limits=limits)
    with pytest.raises(CanonicalJsonError) as right:
        canonical_json(descending, resource_limits=limits)
    assert (
        right.value.code,
        right.value.path,
        right.value.limit,
        right.value.observed,
    ) == (
        left.value.code,
        left.value.path,
        left.value.limit,
        left.value.observed,
    )
    assert left.value.path == "$.z"
    assert left.value.instance_pointer == "/z"


def test_instance_pointer_disambiguates_human_path_collisions() -> None:
    with pytest.raises(CanonicalJsonError) as flat:
        canonical_json(
            {"a.b": None},
            resource_limits=JsonResourceLimits(max_depth=0, max_nodes=2),
        )
    with pytest.raises(CanonicalJsonError) as nested:
        canonical_json(
            {"a": {"b": None}},
            resource_limits=JsonResourceLimits(max_depth=1, max_nodes=3),
        )

    assert flat.value.path == nested.value.path == "$.a.b"
    assert flat.value.instance_pointer == "/a.b"
    assert nested.value.instance_pointer == "/a/b"


@pytest.mark.parametrize(
    ("key", "expected_pointer"),
    [
        ("a/b", "/a~1b"),
        ("m~n", "/m~0n"),
        ("", "/"),
    ],
)
def test_instance_pointer_escapes_rfc_6901_child_tokens(
    key: str,
    expected_pointer: str,
) -> None:
    with pytest.raises(CanonicalJsonError) as failure:
        canonical_json(
            {key: None},
            resource_limits=JsonResourceLimits(max_depth=0, max_nodes=2),
        )

    assert failure.value.path == f"$.{key}"
    assert failure.value.instance_pointer == expected_pointer


def test_scalar_diagnostics_keep_human_path_without_resource_pointer() -> None:
    with pytest.raises(CanonicalJsonError) as failure:
        canonical_json({"a/b": 9_007_199_254_740_992}, resource_limits=LIMITS)

    assert failure.value.code == "unsafe_integer"
    assert failure.value.path == "$.a/b"
    assert failure.value.instance_pointer is None


def test_wide_object_node_limit_precedes_key_validation() -> None:
    limits = JsonResourceLimits(max_depth=2_000, max_nodes=32)
    very_wide: dict[object, object] = {object(): object()}
    for index in range(100_000):
        very_wide[padded_key(index)] = None

    with pytest.raises(CanonicalJsonError) as failure:
        canonical_json(very_wide, resource_limits=limits)
    assert_resource_error(
        failure.value,
        code="json_max_nodes_exceeded",
        instance_pointer="",
        limit=limits.max_nodes,
        observed=limits.max_nodes + 1,
    )
    assert failure.value.path == "$"


def test_unprofiled_kernel_call_preserves_the_existing_wider_domain() -> None:
    assert canonical_json([None] * LIMITS.max_nodes).startswith(b"[")


def test_try_canonical_json_returns_resource_limit_failure() -> None:
    result = try_canonical_json(
        [None],
        resource_limits=JsonResourceLimits(max_depth=0, max_nodes=1),
    )

    assert result.is_err is True
    assert result.code == "json_max_nodes_exceeded"
    assert result.message == "maximum JSON node count is 1"


def nested_array(depth: int) -> object:
    value: object = None
    for _ in range(depth):
        value = [value]
    return value


def assert_resource_error(
    error: CanonicalJsonError,
    *,
    code: str,
    instance_pointer: str,
    limit: int,
    observed: int,
) -> None:
    assert error.code == code
    assert error.instance_pointer == instance_pointer
    assert error.limit == limit
    assert error.observed == observed


def padded_key(index: int) -> str:
    return f"k{index:05d}"
