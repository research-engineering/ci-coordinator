"""Pure bounded projections for supported GitHub Actions YAML fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery._yaml_nodes import (
    YamlMapping,
    YamlNode,
    YamlScalar,
    YamlSequence,
    contains_expression,
    scalar_text,
    walk_scalars,
)
from ci_coordinator.workflow_discovery.summary import (
    ConcurrencySummary,
    DeclaredPermissions,
    MatrixDimension,
    PermissionEntry,
    StepSummary,
)

_SECRET_NAME = re.compile(r"(?<![A-Za-z0-9_])secrets\.([A-Za-z_][A-Za-z0-9_]*)")
_LITERAL_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
PROPOSAL_EVENT_ACTIONS: Final = (
    ("merge_group", ("checks_requested",)),
    ("pull_request", ("opened", "reopened", "synchronize", "ready_for_review")),
    ("push", ()),
)
_PROPOSAL_EVENT_ACTIONS = dict(PROPOSAL_EVENT_ACTIONS)
_DEFAULT_EVENT_ACTIONS: Final = {
    "merge_group": ("checks_requested",),
    "pull_request": ("opened", "reopened", "synchronize"),
    "push": (),
}


@dataclass(frozen=True, slots=True)
class StepProjection:
    steps: tuple[StepSummary, ...] | None
    reason: str | None
    dynamic_uses: bool


def triggers(node: YamlNode | None) -> tuple[tuple[str, ...] | None, str | None]:
    values: list[str]
    if isinstance(node, YamlScalar) and type(node.value) is str:
        values = [node.value]
    elif isinstance(node, YamlSequence):
        values = [
            item.value
            for item in node.items
            if isinstance(item, YamlScalar) and type(item.value) is str
        ]
        if len(values) != len(node.items):
            return None, "unsupported_trigger_syntax"
    elif isinstance(node, YamlMapping):
        values = [key for key, _ in node.entries]
    else:
        return None, "trigger_missing" if node is None else "unsupported_trigger_syntax"
    if not values or any(not value or contains_expression(value) for value in values):
        return None, "dynamic_or_empty_trigger"
    return tuple(sorted(set(values), key=utf16_sort_key)), None


def default_branch_ci_events(
    node: YamlNode | None,
    *,
    default_branch: str,
) -> tuple[str, ...] | None:
    """Return the CI events proven to cover the complete default-branch input domain."""

    trigger_names, reason = triggers(node)
    if reason is not None or trigger_names is None:
        return None
    if not isinstance(node, YamlMapping):
        return tuple(
            event
            for event in _PROPOSAL_EVENT_ACTIONS
            if event in trigger_names and _event_actions_cover(event, None)
        )
    covered: list[str] = []
    for event, _ in PROPOSAL_EVENT_ACTIONS:
        event_node = node.get(event)
        if event_node is not None and _event_covers_default_branch(
            event,
            event_node,
            default_branch=default_branch,
        ):
            covered.append(event)
    return tuple(covered)


def _event_covers_default_branch(
    event: str,
    node: YamlNode,
    *,
    default_branch: str,
) -> bool:
    if isinstance(node, YamlScalar) and node.value is None:
        return _event_actions_cover(event, None)
    if not isinstance(node, YamlMapping):
        return False
    allowed_keys: set[str] = set()
    if event in {"pull_request", "push"}:
        allowed_keys.add("branches")
    required_actions = _PROPOSAL_EVENT_ACTIONS[event]
    if required_actions:
        allowed_keys.add("types")
    if any(key not in allowed_keys for key, _ in node.entries):
        return False
    branches_node = node.get("branches")
    if branches_node is not None and (
        _LITERAL_BRANCH.fullmatch(default_branch) is None
        or _static_texts(branches_node) != (default_branch,)
    ):
        return False
    return _event_actions_cover(event, node.get("types"))


def _event_actions_cover(event: str, types_node: YamlNode | None) -> bool:
    event_types = _DEFAULT_EVENT_ACTIONS[event] if types_node is None else _static_texts(types_node)
    return event_types is not None and set(_PROPOSAL_EVENT_ACTIONS[event]).issubset(event_types)


def _static_texts(node: YamlNode) -> tuple[str, ...] | None:
    values: tuple[str, ...]
    if isinstance(node, YamlScalar) and type(node.value) is str:
        values = (node.value,)
    elif isinstance(node, YamlSequence):
        values = tuple(
            item.value
            for item in node.items
            if isinstance(item, YamlScalar) and type(item.value) is str
        )
        if len(values) != len(node.items):
            return None
    else:
        return None
    if not values or any(not value or contains_expression(value) for value in values):
        return None
    return values


def permissions(
    node: YamlNode | None,
) -> tuple[DeclaredPermissions | None, str | None]:
    if node is None:
        return DeclaredPermissions("absent"), None
    if isinstance(node, YamlScalar) and type(node.value) is str:
        if node.value in {"read-all", "write-all"}:
            return DeclaredPermissions("all", all_level=node.value), None
        return None, "unsupported_permissions_syntax"
    if not isinstance(node, YamlMapping):
        return None, "unsupported_permissions_syntax"
    entries: list[PermissionEntry] = []
    for name, value in node.entries:
        level = scalar_text(value)
        if level is None or contains_expression(level) or level not in {"none", "read", "write"}:
            return None, "dynamic_or_unsupported_permission"
        entries.append(PermissionEntry(name, level))
    return (
        DeclaredPermissions(
            "entries",
            entries=tuple(sorted(entries, key=lambda item: utf16_sort_key(item.name))),
        ),
        None,
    )


def concurrency(
    node: YamlNode | None,
) -> tuple[ConcurrencySummary | None, str | None]:
    if node is None:
        return ConcurrencySummary(None, None), None
    if isinstance(node, YamlScalar) and type(node.value) is str:
        if contains_expression(node.value):
            return None, "dynamic_concurrency"
        return ConcurrencySummary(node.value, None), None
    if not isinstance(node, YamlMapping):
        return None, "unsupported_concurrency_syntax"
    group_node = node.get("group")
    group = scalar_text(group_node)
    cancel_node = node.get("cancel-in-progress")
    cancel = cancel_node.value if isinstance(cancel_node, YamlScalar) else None
    if (
        group is None
        or contains_expression(group)
        or (cancel_node is not None and type(cancel) is not bool)
    ):
        return None, "dynamic_or_unsupported_concurrency"
    return ConcurrencySummary(group, cancel if type(cancel) is bool else None), None


def text_set(
    node: YamlNode | None,
    *,
    absent: tuple[str, ...] = (),
) -> tuple[tuple[str, ...] | None, str | None]:
    if node is None:
        return absent, None
    values: tuple[str, ...]
    if isinstance(node, YamlScalar) and type(node.value) is str:
        values = (node.value,)
    elif isinstance(node, YamlSequence):
        raw = tuple(
            item.value
            for item in node.items
            if isinstance(item, YamlScalar) and type(item.value) is str
        )
        if len(raw) != len(node.items):
            return None, "unsupported_sequence_syntax"
        values = raw
    else:
        return None, "unsupported_sequence_syntax"
    if any(not value or contains_expression(value) for value in values):
        return None, "dynamic_sequence_value"
    return tuple(sorted(set(values), key=utf16_sort_key)), None


def matrix(
    node: YamlNode | None,
) -> tuple[tuple[MatrixDimension, ...] | None, str | None]:
    if node is None:
        return (), None
    if not isinstance(node, YamlMapping) or not node.entries:
        return None, "unsupported_matrix_syntax"
    dimensions: list[MatrixDimension] = []
    for name, value in node.entries:
        if name in {"include", "exclude"} or not isinstance(value, YamlSequence):
            return None, "matrix_include_exclude_or_expression_not_projected"
        scalars = tuple(
            item.value
            for item in value.items
            if isinstance(item, YamlScalar)
            and type(item.value) in {type(None), bool, int, float, str}
        )
        if len(scalars) != len(value.items) or any(
            type(item) is str and contains_expression(item) for item in scalars
        ):
            return None, "dynamic_or_structured_matrix_value"
        dimensions.append(MatrixDimension(name, scalars))
    return tuple(sorted(dimensions, key=lambda item: utf16_sort_key(item.name))), None


def steps(node: YamlNode | None) -> StepProjection:
    if node is None:
        return StepProjection((), None, False)
    if not isinstance(node, YamlSequence):
        return StepProjection(None, "unsupported_steps_syntax", False)
    result: list[StepSummary] = []
    dynamic_uses = False
    for index, item in enumerate(node.items):
        if not isinstance(item, YamlMapping):
            return StepProjection(None, "unsupported_step_syntax", False)
        name_node = item.get("name")
        name = scalar_text(name_node)
        if name_node is not None and name is None:
            return StepProjection(None, "unsupported_step_name", False)
        uses_node = item.get("uses")
        uses = scalar_text(uses_node)
        if uses_node is not None and uses is None:
            return StepProjection(None, "unsupported_step_uses", False)
        uses_dynamic = uses is not None and contains_expression(uses)
        dynamic_uses = dynamic_uses or uses_dynamic
        condition_node = item.get("if")
        condition = scalar_text(condition_node)
        if condition_node is not None and condition is None:
            return StepProjection(None, "unsupported_step_condition", False)
        result.append(
            StepSummary(
                index=index,
                name=name,
                uses=uses,
                uses_dynamic=uses_dynamic,
                has_run=item.get("run") is not None,
                condition=condition,
            )
        )
    return StepProjection(tuple(result), None, dynamic_uses)


def environment(node: YamlNode | None) -> tuple[str | None, str | None]:
    if node is None:
        return None, None
    value = scalar_text(node)
    if isinstance(node, YamlMapping):
        value = scalar_text(node.get("name"))
    if value is None or contains_expression(value):
        return None, "dynamic_or_unsupported_environment"
    return value, None


def service_ids(node: YamlNode | None) -> tuple[tuple[str, ...] | None, str | None]:
    if node is None:
        return (), None
    if not isinstance(node, YamlMapping):
        return None, "unsupported_services_syntax"
    return tuple(sorted((key for key, _ in node.entries), key=utf16_sort_key)), None


def secret_names(node: YamlNode) -> tuple[tuple[str, ...] | None, str | None]:
    names: set[str] = set()
    unresolved = False
    for scalar in walk_scalars(node, skip_mapping_keys=frozenset({"run"})):
        if type(scalar.value) is not str:
            continue
        names.update(_SECRET_NAME.findall(scalar.value))
        if "secrets[" in scalar.value or "secrets.*" in scalar.value:
            unresolved = True
    if isinstance(node, YamlMapping):
        secrets_node = node.get("secrets")
        if isinstance(secrets_node, YamlScalar) and secrets_node.value == "inherit":
            unresolved = True
    if unresolved:
        return None, "dynamic_or_inherited_secret_names"
    return tuple(sorted(names, key=utf16_sort_key)), None
