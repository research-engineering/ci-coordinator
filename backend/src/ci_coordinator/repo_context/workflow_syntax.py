"""Bounded structural admission for GitHub Actions workflow capabilities."""

from __future__ import annotations

import re
from typing import Final

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.repo_context.runner_selector import (
    StaticRunnerSelector,
    WorkflowJobRunnerSelector,
    static_runner_selector,
)
from ci_coordinator.repo_context.workflow_control_plane import (
    control_job_projection_hash,
)
from ci_coordinator.repo_context.workflow_inventory import (
    RevisionWorkflowCapability,
    WorkflowJobAuthority,
    is_workflow_path,
)

MAX_WORKFLOW_FILE_BYTES: Final = 262_144
_MAX_WORKFLOW_NODES: Final = 20_000
_MAX_WORKFLOW_DEPTH: Final = 64
_MAX_SCALAR_BYTES: Final = 65_536
_CORE_TAG_PREFIX: Final = "tag:yaml.org,2002:"
_GITHUB_OWNER: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_GITHUB_REPOSITORY: Final = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_GITHUB_OBJECT_ID: Final = re.compile(r"[0-9a-f]{40}")
_EXPRESSION_DEREFERENCE_CONTEXT: Final = re.compile(
    r"(?<![A-Za-z0-9_.-])([A-Za-z_][A-Za-z0-9_-]*)\s*[.\[]"
)
_EXPRESSION_BARE_CONTEXT: Final = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(github|env|vars|job|jobs|steps|runner|secrets|strategy|matrix|needs|inputs)"
    r"(?![A-Za-z0-9_-])",
    re.ASCII | re.IGNORECASE,
)
_CONTAINER_DIGEST: Final = re.compile(r".+@sha256:[0-9a-f]{64}")


def parse_workflow_capability(
    content: bytes,
    *,
    path: str,
    revision_sha: str,
) -> RevisionWorkflowCapability | None:
    """Extract static job ids, observable names, and top-level triggers."""

    if type(content) is not bytes or len(content) > MAX_WORKFLOW_FILE_BYTES:
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.startswith("\ufeff") or "\x00" in text:
        return None

    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    yaml.allow_duplicate_keys = False
    try:
        root = yaml.compose(text)
        if not isinstance(root, MappingNode):
            return None
        _validate_node_graph(root)
        root_fields = _mapping_fields(root)
        jobs = root_fields.get("jobs")
        triggers = root_fields.get("on")
        if not isinstance(jobs, MappingNode) or triggers is None:
            return None
        loaded_yaml = YAML(typ="safe", pure=True)
        loaded_yaml.version = (1, 2)
        loaded_yaml.allow_duplicate_keys = False
        loaded = loaded_yaml.load(text)
        if type(loaded) is not dict or type(loaded.get("jobs")) is not dict:
            return None
        job_fields = _mapping_fields(jobs)
        job_ids = tuple(sorted(job_fields, key=utf16_sort_key))
        job_needs = _job_needs(job_fields)
        provider_job_names = _provider_job_names(job_fields)
        always_job_ids = _always_job_ids(job_fields)
        trigger_ids = tuple(sorted(_trigger_ids(triggers), key=utf16_sort_key))
        return RevisionWorkflowCapability(
            path=path,
            revision_sha=revision_sha,
            job_ids=job_ids,
            job_needs=job_needs,
            provider_job_names=provider_job_names,
            always_job_ids=always_job_ids,
            triggers=trigger_ids,
            local_reusable_workflow_paths=_local_reusable_workflow_paths(job_fields),
            job_authorities=_job_authorities(
                job_fields,
                raw_jobs=loaded["jobs"],
                raw_workflow=loaded,
            ),
            declares_workflow_environment="env" in root_fields,
            declares_workflow_defaults="defaults" in root_fields,
            job_runner_selectors=_job_runner_selectors(job_fields),
        )
    except (TypeError, ValueError, YAMLError):
        return None


def _validate_node_graph(root: Node) -> None:
    seen: set[int] = set()
    stack: list[tuple[Node, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        identity = id(node)
        if identity in seen:
            raise ValueError("workflow aliases are not admitted")
        seen.add(identity)
        if len(seen) > _MAX_WORKFLOW_NODES or depth > _MAX_WORKFLOW_DEPTH:
            raise ValueError("workflow structure exceeds its bound")
        if type(node.tag) is not str or not node.tag.startswith(_CORE_TAG_PREFIX):
            raise ValueError("workflow contains a custom YAML tag")
        if isinstance(node, ScalarNode):
            if len(node.value.encode("utf-8")) > _MAX_SCALAR_BYTES:
                raise ValueError("workflow scalar exceeds its bound")
            continue
        if isinstance(node, SequenceNode):
            stack.extend((child, depth + 1) for child in node.value)
            continue
        if not isinstance(node, MappingNode):
            raise ValueError("workflow contains an unsupported YAML node")
        stack.extend((value, depth + 1) for key, value in node.value)
        stack.extend((key, depth + 1) for key, _ in node.value)


def _mapping_fields(node: MappingNode) -> dict[str, Node]:
    fields: dict[str, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.tag != f"{_CORE_TAG_PREFIX}str":
            raise ValueError("workflow mapping keys must be strings")
        if not key.value or len(key.value.encode("utf-8")) > 128 or key.value in fields:
            raise ValueError("workflow mapping key is invalid or duplicated")
        fields[key.value] = value
    return fields


def _trigger_ids(node: Node) -> set[str]:
    if isinstance(node, ScalarNode):
        return {_trigger_id(node)}
    if isinstance(node, SequenceNode):
        return {_trigger_id(item) for item in node.value}
    if isinstance(node, MappingNode):
        return set(_mapping_fields(node))
    raise ValueError("workflow trigger declaration is invalid")


def _provider_job_names(
    jobs: dict[str, Node],
) -> tuple[tuple[str, str], ...]:
    names: list[tuple[str, str]] = []
    for job_id, job_node in jobs.items():
        if not isinstance(job_node, MappingNode):
            raise ValueError("workflow job declaration must be a mapping")
        fields = _mapping_fields(job_node)
        name = _static_provider_job_name(fields)
        if name is not None:
            names.append((job_id, name))
    return tuple(sorted(names, key=lambda item: utf16_sort_key(item[0])))


def _always_job_ids(jobs: dict[str, Node]) -> tuple[str, ...]:
    admitted = []
    for job_id, job_node in jobs.items():
        if not isinstance(job_node, MappingNode):
            raise ValueError("workflow job declaration must be a mapping")
        condition = _mapping_fields(job_node).get("if")
        if (
            isinstance(condition, ScalarNode)
            and condition.tag == f"{_CORE_TAG_PREFIX}str"
            and condition.value in {"always()", "${{ always() }}"}
        ):
            admitted.append(job_id)
    return tuple(sorted(admitted, key=utf16_sort_key))


def _job_needs(jobs: dict[str, Node]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    known = set(jobs)
    dependencies: list[tuple[str, tuple[str, ...]]] = []
    for job_id, job_node in jobs.items():
        if not isinstance(job_node, MappingNode):
            raise ValueError("workflow job declaration must be a mapping")
        needs = _static_needs(_mapping_fields(job_node).get("needs"))
        if any(dependency not in known for dependency in needs):
            raise ValueError("workflow job depends on an unknown static job")
        dependencies.append((job_id, needs))
    return tuple(sorted(dependencies, key=lambda item: utf16_sort_key(item[0])))


def _job_runner_selectors(
    jobs: dict[str, Node],
) -> tuple[WorkflowJobRunnerSelector, ...]:
    selectors: list[WorkflowJobRunnerSelector] = []
    for job_id, job_node in jobs.items():
        if not isinstance(job_node, MappingNode):
            raise ValueError("workflow job declaration must be a mapping")
        selector = _static_runner_selector(_mapping_fields(job_node).get("runs-on"))
        if selector is not None:
            selectors.append(WorkflowJobRunnerSelector(job_id=job_id, selector=selector))
    return tuple(sorted(selectors, key=lambda item: utf16_sort_key(item.job_id)))


def _static_runner_selector(node: Node | None) -> StaticRunnerSelector | None:
    if isinstance(node, ScalarNode):
        label = _static_runner_text(node)
        return None if label is None else static_runner_selector(labels=(label,), group=None)
    if isinstance(node, SequenceNode):
        labels = tuple(_static_runner_text(item) for item in node.value)
        if not labels or any(label is None for label in labels):
            return None
        return static_runner_selector(
            labels=tuple(label for label in labels if label is not None),
            group=None,
        )
    if not isinstance(node, MappingNode):
        return None
    fields = _mapping_fields(node)
    if not fields or not set(fields).issubset({"group", "labels"}):
        return None
    group = _static_runner_text(fields.get("group"))
    label = _static_runner_text(fields.get("labels"))
    if ("group" in fields and group is None) or ("labels" in fields and label is None):
        return None
    return static_runner_selector(
        labels=() if label is None else (label,),
        group=group,
    )


def _static_runner_text(node: Node | None) -> str | None:
    value = None if node is None else node.value
    if (
        not isinstance(node, ScalarNode)
        or node.tag != f"{_CORE_TAG_PREFIX}str"
        or type(value) is not str
        or not value
        or "${{" in value
    ):
        return None
    return value


def _local_reusable_workflow_paths(jobs: dict[str, Node]) -> tuple[str, ...]:
    paths: set[str] = set()
    for job_node in jobs.values():
        if not isinstance(job_node, MappingNode):
            raise ValueError("workflow job declaration must be a mapping")
        uses = _mapping_fields(job_node).get("uses")
        if uses is None:
            continue
        if (
            not isinstance(uses, ScalarNode)
            or uses.tag != f"{_CORE_TAG_PREFIX}str"
            or not uses.value
            or len(uses.value.encode("utf-8")) > 512
            or "${{" in uses.value
        ):
            raise ValueError("reusable workflow target must be static bounded text")
        if uses.value.startswith(("./", "$/")):
            path = uses.value[2:]
        else:
            if not _is_immutable_external_reusable_workflow(uses.value):
                raise ValueError("external reusable workflow target must use an immutable revision")
            continue
        if not is_workflow_path(path):
            raise ValueError("local reusable workflow target is invalid")
        paths.add(path)
    return tuple(sorted(paths, key=utf16_sort_key))


def _is_immutable_external_reusable_workflow(value: str) -> bool:
    target, separator, revision = value.rpartition("@")
    parts = target.split("/", 2)
    return (
        separator == "@"
        and len(parts) == 3
        and _GITHUB_OWNER.fullmatch(parts[0]) is not None
        and _GITHUB_REPOSITORY.fullmatch(parts[1]) is not None
        and is_workflow_path(parts[2])
        and _GITHUB_OBJECT_ID.fullmatch(revision) is not None
    )


def _job_authorities(
    jobs: dict[str, Node],
    *,
    raw_jobs: dict[object, object],
    raw_workflow: dict[object, object],
) -> tuple[WorkflowJobAuthority, ...]:
    authorities: list[WorkflowJobAuthority] = []
    for job_id, job_node in jobs.items():
        if not isinstance(job_node, MappingNode):
            raise ValueError("workflow job declaration must be a mapping")
        fields = _mapping_fields(job_node)
        _require_immutable_container(fields.get("container"))
        _require_immutable_services(fields.get("services"))
        _require_explicit_job_secrets(fields.get("secrets"))
        steps = fields.get("steps")
        step_declares_continue_on_error = False
        if steps is not None:
            if not isinstance(steps, SequenceNode):
                raise ValueError("workflow steps must be a sequence")
            for step_node in steps.value:
                if not isinstance(step_node, MappingNode):
                    raise ValueError("workflow step must be a mapping")
                step = _mapping_fields(step_node)
                step_declares_continue_on_error |= "continue-on-error" in step
                uses = step.get("uses")
                if uses is not None:
                    _step_action(uses)
        condition = _optional_scalar_text(
            fields.get("if"),
            "workflow job condition",
            4_096,
        )
        raw_job = raw_jobs.get(job_id)
        raw_root = (
            {key: value for key, value in raw_workflow.items() if type(key) is str}
            if all(type(key) is str for key in raw_workflow)
            else None
        )
        projection_hash = (
            None if raw_root is None else control_job_projection_hash(raw_job, workflow=raw_root)
        )
        if projection_hash is None:
            raise ValueError("workflow control projection is invalid")
        authorities.append(
            WorkflowJobAuthority(
                job_id=job_id,
                control_projection_hash=projection_hash,
                condition_contexts=tuple(
                    sorted(
                        _expression_contexts(condition or ""),
                        key=utf16_sort_key,
                    )
                ),
                declares_continue_on_error=(
                    "continue-on-error" in fields or step_declares_continue_on_error
                ),
            )
        )
    return tuple(sorted(authorities, key=lambda item: utf16_sort_key(item.job_id)))


def _expression_contexts(value: str) -> set[str]:
    code = _without_single_quoted_strings(value)
    contexts = (
        *_EXPRESSION_DEREFERENCE_CONTEXT.findall(code),
        *_EXPRESSION_BARE_CONTEXT.findall(code),
    )
    return {context.lower() for context in contexts}


def _require_explicit_job_secrets(node: Node | None) -> None:
    if node is not None and not isinstance(node, MappingNode):
        raise ValueError("workflow job secrets must use an explicit mapping")


def _without_single_quoted_strings(value: str) -> str:
    result: list[str] = []
    inside = False
    index = 0
    while index < len(value):
        character = value[index]
        if character != "'":
            result.append(" " if inside else character)
            index += 1
            continue
        if inside and index + 1 < len(value) and value[index + 1] == "'":
            result.extend((" ", " "))
            index += 2
            continue
        inside = not inside
        result.append(" ")
        index += 1
    return "".join(result)


def _step_action(node: Node) -> str | None:
    value = _scalar_text(node, "workflow action reference", 512)
    if "${{" in value:
        raise ValueError("workflow action reference must be static")
    if value.startswith(("./", "$/")):
        return _local_action_path(value)
    if value.startswith("docker://"):
        if _CONTAINER_DIGEST.fullmatch(value.removeprefix("docker://")) is None:
            raise ValueError("container action must use an immutable digest")
        return None
    target, separator, revision = value.rpartition("@")
    parts = target.split("/")
    if (
        separator != "@"
        or len(parts) < 2
        or _GITHUB_OWNER.fullmatch(parts[0]) is None
        or _GITHUB_REPOSITORY.fullmatch(parts[1]) is None
        or any(not part or part in {".", ".."} or "\\" in part for part in parts[2:])
        or _GITHUB_OBJECT_ID.fullmatch(revision) is None
    ):
        raise ValueError("external workflow action must use an immutable revision")
    return None


def _local_action_path(value: str) -> str:
    prefix = value[:2]
    path = value[2:]
    if (
        "\\" in path
        or (prefix == "$/" and "@" in path)
        or any(not part or part in {".", ".."} for part in path.split("/"))
    ):
        raise ValueError("local workflow action path is invalid")
    return path


def _require_immutable_container(node: Node | None) -> None:
    if node is None:
        return
    image: Node | None = node
    if isinstance(node, MappingNode):
        image = _mapping_fields(node).get("image")
    if image is None:
        raise ValueError("workflow container must declare an image")
    value = _scalar_text(image, "workflow container image", 512)
    if "${{" in value or _CONTAINER_DIGEST.fullmatch(value) is None:
        raise ValueError("workflow container image must use an immutable digest")


def _require_immutable_services(node: Node | None) -> None:
    if node is None:
        return
    if not isinstance(node, MappingNode):
        raise ValueError("workflow services must be a mapping")
    for service in _mapping_fields(node).values():
        if not isinstance(service, MappingNode):
            raise ValueError("workflow service must be a mapping")
        image = _mapping_fields(service).get("image")
        if image is None:
            raise ValueError("workflow service must declare an image")
        value = _scalar_text(image, "workflow service image", 512)
        if "${{" in value or _CONTAINER_DIGEST.fullmatch(value) is None:
            raise ValueError("workflow service image must use an immutable digest")


def _optional_scalar_text(
    node: Node | None,
    field_name: str,
    maximum_bytes: int,
) -> str | None:
    return None if node is None else _scalar_text(node, field_name, maximum_bytes)


def _scalar_text(node: Node, field_name: str, maximum_bytes: int) -> str:
    value = node.value if isinstance(node, ScalarNode) else None
    if (
        type(value) is not str
        or node.tag != f"{_CORE_TAG_PREFIX}str"
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{field_name} must be bounded static text")
    return value


def _static_needs(node: Node | None) -> tuple[str, ...]:
    if node is None:
        return ()
    raw = node.value if isinstance(node, SequenceNode) else [node]
    values: list[str] = []
    for item in raw:
        if (
            not isinstance(item, ScalarNode)
            or item.tag != f"{_CORE_TAG_PREFIX}str"
            or not item.value
            or len(item.value.encode("utf-8")) > 128
            or "${{" in item.value
        ):
            raise ValueError("workflow job dependency must be a static job id")
        values.append(item.value)
    canonical = tuple(sorted(set(values), key=utf16_sort_key))
    if len(canonical) != len(values):
        raise ValueError("workflow job dependencies must be unique")
    return canonical


def _static_provider_job_name(fields: dict[str, Node]) -> str | None:
    name_node = fields.get("name")
    if (
        name_node is None
        or "uses" in fields
        or _declares_matrix(fields.get("strategy"))
        or not isinstance(name_node, ScalarNode)
        or name_node.tag != f"{_CORE_TAG_PREFIX}str"
    ):
        return None
    name = name_node.value
    if (
        not isinstance(name, str)
        or not name
        or len(name.encode("utf-8")) > 256
        or "${{" in name
        or any(0xD800 <= ord(character) <= 0xDFFF for character in name)
    ):
        return None
    return name


def _declares_matrix(strategy: Node | None) -> bool:
    if strategy is None:
        return False
    if not isinstance(strategy, MappingNode):
        return True
    return "matrix" in _mapping_fields(strategy)


def _trigger_id(node: Node) -> str:
    if not isinstance(node, ScalarNode) or node.tag != f"{_CORE_TAG_PREFIX}str":
        raise ValueError("workflow trigger must be a string")
    value = node.value
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
        raise ValueError("workflow trigger is invalid")
    return value
