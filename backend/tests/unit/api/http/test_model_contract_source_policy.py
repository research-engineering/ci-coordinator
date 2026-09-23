from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

from ci_coordinator.api.http.contracts import PlanRequestBody
from ci_coordinator.api.http.routers.provider_inventory import (
    RepositoryScopeResponse as ProviderRepositoryScopeResponse,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[4]
_HTTP_ROOT = _BACKEND_ROOT / "src" / "ci_coordinator" / "api" / "http"
_MODEL_POLICY_PATH = _HTTP_ROOT / "model_contracts.py"
_AFTER_VALIDATORS = {
    ("ci_economics_catalog_contracts.py", "_canonical_source_cursor"),
    ("ci_economics_contracts.py", "_canonical_attempt_cursor"),
    ("production_cutover_contracts.py", "_utf8"),
}
_BEFORE_FIELD_VALIDATORS = {
    (
        "routers/operator_controls.py",
        "ForceFullCIOverrideRequest",
        "parse_iso8601_expiry_text",
        ("expires_at",),
    )
}
_FIELD_ALIAS_DECLARATIONS = {
    (
        "ci_economics_measurement_contracts.py",
        "EconomicsReconciliationSourceResponse",
        "source_kind",
        "sourceKind",
    ),
    (
        "ci_economics_source_contracts.py",
        "EconomicsProviderSourceResponse",
        "source_kind",
        "sourceKind",
    ),
    (
        "routers/workbench.py",
        "NativeProfileExecutionResponse",
        "execution_kind",
        "executionKind",
    ),
    (
        "routers/workbench.py",
        "ShardedProfileCapacityResponse",
        "execution_kind",
        "executionKind",
    ),
}
_ALLOWED_PYDANTIC_IMPORTS = {
    "AfterValidator",
    "Field",
    "JsonValue",
    "ValidationError",
    "field_validator",
    "model_validator",
}
_PYDANTIC_POLICY_CALLABLES = {
    "AfterValidator",
    "Field",
    "field_validator",
    "model_validator",
}

if TYPE_CHECKING:
    PlanRequestBody(
        schemaVersion="dynamic-ci-plan-request/v2",
        requestId="request-1",
        installationId=1,
        repositoryId=2,
        owner="example",
        repository="repository",
        eventName="pull_request",
        ref="refs/pull/7/merge",
        baseSha="a" * 40,
        headSha="b" * 40,
        executionSha="c" * 40,
        workflowRunId=3,
        runAttempt=1,
    )
    PlanRequestBody(  # type: ignore[call-arg]
        schema_version="dynamic-ci-plan-request/v2",
        request_id="request-1",
        installation_id=1,
        repository_id=2,
        owner="example",
        repository="repository",
        event_name="pull_request",
        ref="refs/pull/7/merge",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=3,
        run_attempt=1,
    )
    ProviderRepositoryScopeResponse(installation_id=1, repository_id=2)


def test_static_policy_rejects_local_config_alias_and_dump_drift() -> None:
    violations: list[str] = []
    policy_classes: set[str] = set()
    after_validators: set[tuple[str, str]] = set()
    before_validators: set[tuple[str, str, str, tuple[str, ...]]] = set()
    field_aliases: set[tuple[str, str, str, str]] = set()
    field_alias_call_count = 0
    for path in sorted(_HTTP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        if path == _MODEL_POLICY_PATH:
            policy_classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
            continue
        relative_path = path.relative_to(_HTTP_ROOT).as_posix()
        after_validators.update(_after_validators(tree, relative_path))
        before_validators.update(_before_field_validators(tree, relative_path))
        field_aliases.update(_field_alias_declarations(tree, relative_path))
        violations.extend(
            f"{path}:{line}: {violation}"
            for line, violation in _pydantic_callable_use_violations(tree)
        )
        for node in ast.walk(tree):
            violations.extend(
                f"{path}:{getattr(node, 'lineno', 0)}: {violation}"
                for violation in _pydantic_import_violations(node)
            )
            if (
                isinstance(node, ast.Call)
                and _call_name(node) == "Field"
                and any(keyword.arg == "alias" for keyword in node.keywords)
            ):
                field_alias_call_count += 1
            if (
                isinstance(node, ast.Call)
                and _call_name(node) == "Field"
                and any(keyword.arg == "strict" for keyword in node.keywords)
            ):
                violations.append(f"{path}:{node.lineno}: field-local strict policy")
            if isinstance(node, ast.Call) and _call_name(node) in {
                "model_dump",
                "model_dump_json",
            }:
                violations.append(f"{path}:{node.lineno}: direct model dump")
            if isinstance(node, ast.Call) and _call_name(node) == "model_validate":
                overrides = sorted(
                    keyword.arg
                    for keyword in node.keywords
                    if keyword.arg in {"by_alias", "by_name", "extra", "from_attributes", "strict"}
                )
                if overrides:
                    violations.append(
                        f"{path}:{node.lineno}: model_validate policy override {overrides}"
                    )
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and "model_config" in (
                _assigned_names(node)
            ):
                violations.append(f"{path}:{node.lineno}: local model_config")
            if isinstance(node, ast.ClassDef) and node.keywords:
                violations.append(f"{path}:{node.lineno}: class keyword policy")
            if isinstance(node, ast.keyword) and node.arg == "populate_by_name":
                violations.append(f"{path}:{node.lineno}: populate_by_name")

    assert policy_classes == {
        "_WireModel",
        "ProjectedResponseModel",
        "RequestModel",
        "ResponseModel",
    }
    assert after_validators == _AFTER_VALIDATORS
    assert before_validators == _BEFORE_FIELD_VALIDATORS
    assert field_aliases == _FIELD_ALIAS_DECLARATIONS
    assert field_alias_call_count == len(field_aliases)
    assert violations == []


def test_pydantic_import_policy_is_closed_over_alias_and_module_forms() -> None:
    admitted = ast.parse("from pydantic import ValidationError")
    assert [
        violation for node in ast.walk(admitted) for violation in _pydantic_import_violations(node)
    ] == []

    cases = {
        "from pydantic import Field as PField": "aliased pydantic import: ['Field']",
        "from pydantic import ValidationError as Error": (
            "aliased pydantic import: ['ValidationError']"
        ),
        "from pydantic import TypeAdapter": "unowned pydantic import: ['TypeAdapter']",
        "from pydantic.fields import FieldInfo": "pydantic submodule import: pydantic.fields",
        "import pydantic": "direct pydantic module import",
        "import pydantic.fields": "direct pydantic module import",
    }

    for source, expected in cases.items():
        tree = ast.parse(source)
        observed = [
            violation for node in ast.walk(tree) for violation in _pydantic_import_violations(node)
        ]
        assert observed == [expected]


def test_pydantic_policy_callables_cannot_escape_or_be_shadowed() -> None:
    cases = {
        "validator = AfterValidator": (
            "pydantic policy callable escaped direct call: AfterValidator"
        ),
        "pfield = Field": "pydantic policy callable escaped direct call: Field",
        "Field = factory": "pydantic policy callable shadowed: Field",
        "def Field(): pass": "pydantic policy callable shadowed: Field",
        "def validate(field_validator): pass": (
            "pydantic policy callable shadowed: field_validator"
        ),
    }

    for source, expected in cases.items():
        observed = [
            violation for _, violation in _pydantic_callable_use_violations(ast.parse(source))
        ]
        assert observed == [expected]


def _call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _pydantic_import_violations(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        if any(item.name == "pydantic" or item.name.startswith("pydantic.") for item in node.names):
            return ("direct pydantic module import",)
        return ()
    if not isinstance(node, ast.ImportFrom) or node.module is None:
        return ()
    if node.module == "pydantic":
        violations: list[str] = []
        aliased = sorted(item.name for item in node.names if item.asname is not None)
        if aliased:
            violations.append(f"aliased pydantic import: {aliased}")
        unowned = sorted(
            item.name for item in node.names if item.name not in _ALLOWED_PYDANTIC_IMPORTS
        )
        if unowned:
            violations.append(f"unowned pydantic import: {unowned}")
        return tuple(violations)
    if node.module.startswith("pydantic."):
        return (f"pydantic submodule import: {node.module}",)
    return ()


def _pydantic_callable_use_violations(tree: ast.AST) -> tuple[tuple[int, str], ...]:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _PYDANTIC_POLICY_CALLABLES:
            if isinstance(node.ctx, ast.Store):
                violations.append((node.lineno, f"pydantic policy callable shadowed: {node.id}"))
                continue
            parent = parents.get(node)
            if not (isinstance(parent, ast.Call) and parent.func is node):
                violations.append(
                    (
                        node.lineno,
                        f"pydantic policy callable escaped direct call: {node.id}",
                    )
                )
        if isinstance(node, ast.arg) and node.arg in _PYDANTIC_POLICY_CALLABLES:
            violations.append((node.lineno, f"pydantic policy callable shadowed: {node.arg}"))
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name in _PYDANTIC_POLICY_CALLABLES
        ):
            violations.append((node.lineno, f"pydantic policy callable shadowed: {node.name}"))
    return tuple(violations)


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {target.id for target in targets if isinstance(target, ast.Name)}


def _after_validators(tree: ast.Module, relative_path: str) -> set[tuple[str, str]]:
    validators: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "AfterValidator":
            continue
        if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
            validators.add((relative_path, "<invalid>"))
            continue
        validators.add((relative_path, node.args[0].id))
    return validators


def _before_field_validators(
    tree: ast.Module,
    relative_path: str,
) -> set[tuple[str, str, str, tuple[str, ...]]]:
    validators: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        for function in (
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            for decorator in function.decorator_list:
                if (
                    not isinstance(decorator, ast.Call)
                    or _call_name(decorator) != "field_validator"
                ):
                    continue
                mode = next(
                    (
                        keyword.value.value
                        for keyword in decorator.keywords
                        if keyword.arg == "mode"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ),
                    None,
                )
                if mode != "before":
                    continue
                fields = tuple(
                    argument.value
                    for argument in decorator.args
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                )
                validators.add((relative_path, class_node.name, function.name, fields))
    return validators


def _field_alias_declarations(
    tree: ast.Module,
    relative_path: str,
) -> set[tuple[str, str, str, str]]:
    aliases: set[tuple[str, str, str, str]] = set()
    for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        for assignment in class_node.body:
            if (
                not isinstance(assignment, ast.AnnAssign)
                or not isinstance(assignment.target, ast.Name)
                or not isinstance(assignment.value, ast.Call)
                or _call_name(assignment.value) != "Field"
            ):
                continue
            alias = next(
                (
                    keyword.value.value
                    for keyword in assignment.value.keywords
                    if keyword.arg == "alias"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            if alias is not None:
                aliases.add((relative_path, class_node.name, assignment.target.id, alias))
    return aliases
