from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Literal

from scripts.bounded_process import spawn
from scripts.dev_environment.environment import (
    DependencyScope,
    ManagedDependencyBorrow,
    borrow_managed_dependencies,
    current_managed_process,
    managed_dependency_process_scope,
    managed_process_invocation,
)
from scripts.diagram_process import DiagramCancellation, cancellation_signals
from scripts.proofkit_common import as_array, as_object, read_json_object

REPO_ROOT = Path(__file__).resolve().parent.parent
QUALITY_PLAN_RELATIVE_PATH = "proofkit/quality-plan.v1.json"
WITNESS_PLAN_RELATIVE_PATH = "proofkit/witness-plan-input.json"
MAX_GITHUB_JOB_TIMEOUT_MINUTES = 360
MAX_COMMAND_OUTPUT_BYTES = 16 * 1024 * 1024
MANAGED_DEPENDENCY_ARGUMENT = "--managed-dependency-context"
_MANAGED_ADAPTER_COMMANDS = frozenset(
    {
        "python.lock-check",
        "python.install-check",
        "dependency.check",
        "requirements.admission",
        "matrix.admission",
        "self-ci.check",
        "architecture.ownership",
        "documentation.graph",
        "documentation.graph-falsifiers",
        "documentation.diagrams-inventory",
        "selective.plan",
        "target-control-bundle.check",
        "text.policy",
        "proofkit.verify",
        "python.package-check",
        "python.lint",
        "python.typecheck",
        "python.import-boundary",
        "secret.scan",
        "python.dependency-usage",
        "python.coverage",
        "python.persistence-test",
        "repository.json",
        "workflow.lint",
        "observability.rules",
        "python.test",
    }
)
_DEPENDENCY_INSTALL_SCOPES: dict[str, DependencyScope] = {
    "python.install-check": "backend",
    "frontend.install": "frontend",
}
_ENVIRONMENT_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
_SENSITIVE_ENVIRONMENT_TOKEN = re.compile(
    r"(?:^|_)(?:ACCESS_KEY|API_KEY|AUTH|COOKIE|CREDENTIALS?|PASSWD|PASSWORD|"
    r"PRIVATE_KEY|SECRET|TOKEN)(?:_|$)"
)


@dataclass(frozen=True, slots=True)
class WitnessCommand:
    cache_policy: str
    command_id: str
    credential_class: str
    environment_allowlist: tuple[str, ...]
    environment_classes: tuple[str, ...]
    environment_inherit: str
    argv: tuple[str, ...]
    cwd: Path
    network_policy: str
    timeout_ms: int


@dataclass(frozen=True, slots=True)
class QualityPlan:
    branch_head_command_ids: tuple[str, ...]
    commands: Mapping[str, WitnessCommand]
    direct_mutation_job_reserve_ms: int
    direct_mutation_job_timeout_minutes: int
    local_command_ids: tuple[str, ...]
    portable_command_ids: tuple[str, ...]
    orchestration_reserve_ms: int
    plan_id: str

    def local_commands(self) -> tuple[WitnessCommand, ...]:
        return tuple(self.commands[command_id] for command_id in self.local_command_ids)

    def portable_commands(self) -> tuple[WitnessCommand, ...]:
        return tuple(self.commands[command_id] for command_id in self.portable_command_ids)

    def branch_head_commands(self) -> tuple[WitnessCommand, ...]:
        command_ids = (*self.local_command_ids, *self.branch_head_command_ids)
        return tuple(self.commands[command_id] for command_id in command_ids)


CommandExecutor = Callable[[WitnessCommand], None]


class QualityPlanUsageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QualitySelection:
    mode: Literal["local", "portable"]
    commands: tuple[WitnessCommand, ...]

    @property
    def write_scopes(self) -> tuple[DependencyScope, ...]:
        return tuple(
            _DEPENDENCY_INSTALL_SCOPES[command.command_id]
            for command in self.commands
            if command.command_id in _DEPENDENCY_INSTALL_SCOPES
        )

    @property
    def sha256(self) -> str:
        payload = {
            "mode": self.mode,
            "commands": [{**asdict(command), "cwd": str(command.cwd)} for command in self.commands],
            "dependencyEffects": [
                [command.command_id, _DEPENDENCY_INSTALL_SCOPES[command.command_id]]
                for command in self.commands
                if command.command_id in _DEPENDENCY_INSTALL_SCOPES
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def select_quality_plan(arguments: Sequence[str], repo_root: Path = REPO_ROOT) -> QualitySelection:
    values = list(arguments)
    if values not in ([], ["local"], ["portable"]):
        raise QualityPlanUsageError("usage: python -m scripts.quality_plan [local|portable]")
    plan = load_quality_plan(repo_root)
    selection = QualitySelection(
        "portable" if values == ["portable"] else "local",
        plan.portable_commands() if values == ["portable"] else plan.local_commands(),
    )
    if selection.mode == "portable" and selection.write_scopes:
        raise ValueError("portable quality commands must not install dependencies")
    return selection


def load_quality_plan(repo_root: Path = REPO_ROOT) -> QualityPlan:
    raw_plan = read_json_object(repo_root / QUALITY_PLAN_RELATIVE_PATH)
    _exact_keys(
        raw_plan,
        (
            "branchHeadAdditionalCommandIds",
            "directMutationJob",
            "localCommandIds",
            "orchestrationReserveMs",
            "planId",
            "portableCommandIds",
            "schemaVersion",
        ),
        "quality plan",
    )
    if raw_plan.get("schemaVersion") != 1:
        raise ValueError("quality plan schemaVersion must equal 1")
    plan_id = _nonempty_string(raw_plan.get("planId"), "quality plan id")
    reserve = raw_plan.get("orchestrationReserveMs")
    if type(reserve) is not int or reserve < 0:
        raise ValueError("quality plan orchestrationReserveMs must be a non-negative integer")
    direct_mutation_job = as_object(
        raw_plan.get("directMutationJob"),
        "direct mutation job",
    )
    _exact_keys(
        direct_mutation_job,
        ("reserveMs", "timeoutMinutes"),
        "direct mutation job",
    )
    direct_mutation_job_reserve_ms = direct_mutation_job.get("reserveMs")
    if type(direct_mutation_job_reserve_ms) is not int or direct_mutation_job_reserve_ms <= 0:
        raise ValueError("direct mutation job reserveMs must be a positive integer")
    direct_mutation_job_timeout_minutes = direct_mutation_job.get("timeoutMinutes")
    if (
        type(direct_mutation_job_timeout_minutes) is not int
        or direct_mutation_job_timeout_minutes <= 0
        or direct_mutation_job_timeout_minutes > MAX_GITHUB_JOB_TIMEOUT_MINUTES
    ):
        raise ValueError("direct mutation job timeoutMinutes must be within the GitHub job limit")

    local_ids = _ordered_unique_strings(raw_plan.get("localCommandIds"), "local command ids")
    branch_ids = _ordered_unique_strings(
        raw_plan.get("branchHeadAdditionalCommandIds"),
        "branch-head command ids",
    )
    portable_ids = _ordered_unique_strings(
        raw_plan.get("portableCommandIds"),
        "portable command ids",
    )
    if local_ids[:2] != ("python.lock-check", "python.install-check"):
        raise ValueError("quality plan must begin with lock admission and environment install")
    overlap = sorted(set(local_ids) & set(branch_ids))
    if overlap:
        raise ValueError(f"quality plan repeats commands across phases: {', '.join(overlap)}")
    if "quality.branch-head" in {*local_ids, *branch_ids}:
        raise ValueError("quality plan must not recursively invoke quality.branch-head")

    witness_plan = read_json_object(repo_root / WITNESS_PLAN_RELATIVE_PATH)
    commands = _command_catalog(witness_plan, repo_root)
    unknown = sorted(({*local_ids, *branch_ids, *portable_ids}) - commands.keys())
    if unknown:
        raise ValueError(f"quality plan references unknown commands: {', '.join(unknown)}")
    branch_head_envelope = commands.get("quality.branch-head")
    if branch_head_envelope is None:
        raise ValueError("quality plan requires a quality.branch-head command envelope")
    branch_command_ids = (*local_ids, *branch_ids)
    child_environment_inputs = {
        name
        for command_id in branch_command_ids
        for name in commands[command_id].environment_allowlist
    }
    dropped_environment_inputs = sorted(
        child_environment_inputs - set(branch_head_envelope.environment_allowlist)
    )
    if dropped_environment_inputs:
        raise ValueError(
            "quality.branch-head cannot forward child environment inputs: "
            + ", ".join(dropped_environment_inputs)
        )
    required_envelope_ms = (
        sum(commands[command_id].timeout_ms for command_id in branch_command_ids) + reserve
    )
    if branch_head_envelope.timeout_ms < required_envelope_ms:
        raise ValueError("quality.branch-head timeout does not preserve orchestration reserve")
    unsafe_portable = [
        command_id
        for command_id in portable_ids
        if commands[command_id].network_policy != "none"
        or any(
            environment_class.endswith("external")
            for environment_class in commands[command_id].environment_classes
        )
    ]
    if unsafe_portable:
        raise ValueError(
            "portable quality commands require an external provider: " + ", ".join(unsafe_portable)
        )
    return QualityPlan(
        branch_head_command_ids=branch_ids,
        commands=commands,
        direct_mutation_job_reserve_ms=direct_mutation_job_reserve_ms,
        direct_mutation_job_timeout_minutes=direct_mutation_job_timeout_minutes,
        local_command_ids=local_ids,
        portable_command_ids=portable_ids,
        orchestration_reserve_ms=reserve,
        plan_id=plan_id,
    )


def execute_commands(
    commands: Sequence[WitnessCommand],
    *,
    environment: Mapping[str, str] | None = None,
    executor: CommandExecutor | None = None,
    managed_dependencies: ManagedDependencyBorrow | None = None,
) -> None:
    source_environment = os.environ if environment is None else environment
    selected_executor = (
        (lambda command: _execute_command(command, source_environment=source_environment))
        if executor is None
        else executor
    )
    for command in commands:
        started = monotonic()
        succeeded = False
        try:
            process_scope = current_managed_process()
            if process_scope is not None:
                process_scope.assert_running()
            scope = _DEPENDENCY_INSTALL_SCOPES.get(command.command_id)
            if managed_dependencies is not None and scope is not None:
                managed_dependencies.begin(scope)
            selected_executor(command)
            if process_scope is not None:
                process_scope.assert_running()
            if managed_dependencies is not None and scope is not None:
                managed_dependencies.complete(scope)
            if process_scope is not None:
                process_scope.assert_running()
            succeeded = True
        finally:
            print(
                json.dumps(
                    {
                        "qualityCommand": command.command_id,
                        "elapsedSeconds": monotonic() - started,
                        "succeeded": succeeded,
                    }
                ),
                flush=True,
            )


def project_command_environment(
    command: WitnessCommand,
    source_environment: Mapping[str, str],
) -> dict[str, str]:
    if command.environment_inherit == "none":
        return {}
    if command.environment_inherit != "allowlist":
        raise ValueError(f"witness command {command.command_id} uses invalid inheritance")
    return {
        name: source_environment[name]
        for name in command.environment_allowlist
        if name in source_environment
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    cancellation: DiagramCancellation | None = None
    try:
        context: str | None = None
        if MANAGED_DEPENDENCY_ARGUMENT in arguments:
            if (
                len(arguments) < 2
                or arguments[-2] != MANAGED_DEPENDENCY_ARGUMENT
                or arguments.count(MANAGED_DEPENDENCY_ARGUMENT) != 1
            ):
                raise QualityPlanUsageError("invalid managed quality invocation")
            context = arguments[-1]
            arguments = arguments[:-2]
        selection = select_quality_plan(arguments, REPO_ROOT)
        if context is None:
            execute_commands(selection.commands)
        else:
            with (
                cancellation_signals() as cancellation,
                borrow_managed_dependencies(
                    REPO_ROOT,
                    context,
                    selection_sha256=selection.sha256,
                    write_scopes=selection.write_scopes,
                    environment=os.environ,
                ) as borrow,
                managed_dependency_process_scope(borrow, selection.sha256, cancellation),
            ):
                execute_commands(selection.commands, managed_dependencies=borrow)
    except QualityPlanUsageError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1 if cancellation is None else cancellation.exit_code(1)
    return 0 if cancellation is None else cancellation.exit_code(0)


def _command_catalog(
    witness_plan: Mapping[str, object], repo_root: Path
) -> dict[str, WitnessCommand]:
    admitted_policies = _admitted_environment_policies(witness_plan)
    commands: dict[str, WitnessCommand] = {}
    for raw_command in as_array(witness_plan.get("commands"), "witness commands"):
        command = as_object(raw_command, "witness command")
        command_id = _nonempty_string(command.get("id"), "witness command id")
        if command_id in commands:
            raise ValueError(f"duplicate witness command id: {command_id}")
        argv = _ordered_strings(command.get("argv"), f"{command_id} argv")
        relative_cwd = _nonempty_string(command.get("cwd"), f"{command_id} cwd")
        cwd = (repo_root / relative_cwd).resolve()
        if not cwd.is_relative_to(repo_root.resolve()) or not cwd.is_dir():
            raise ValueError(f"{command_id} cwd is not a repository directory")
        timeout_ms = command.get("timeoutMs")
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError(f"{command_id} timeoutMs must be a positive integer")
        environment = as_object(command.get("environment"), f"{command_id} environment")
        _exact_keys(
            environment,
            ("allowlist", "classes", "inherit"),
            f"{command_id} environment",
        )
        environment_allowlist = _environment_allowlist(
            environment.get("allowlist"),
            command_id,
        )
        environment_classes = _ordered_unique_strings(
            environment.get("classes"),
            f"{command_id} environment classes",
        )
        environment_inherit = _nonempty_string(
            environment.get("inherit"),
            f"{command_id} environment inheritance",
        )
        if environment_inherit not in {"allowlist", "none"}:
            raise ValueError(f"{command_id} environment inheritance must equal allowlist or none")
        if environment_inherit == "none" and environment_allowlist:
            raise ValueError(f"{command_id} inherit=none requires an empty allowlist")
        if environment_inherit == "allowlist" and not environment_allowlist:
            raise ValueError(f"{command_id} inherit=allowlist requires allowed variables")
        credential_class = _nonempty_string(
            command.get("credentialClass"),
            f"{command_id} credential class",
        )
        network_policy = _nonempty_string(
            command.get("networkPolicy"),
            f"{command_id} network policy",
        )
        cache_policy = _nonempty_string(
            command.get("cachePolicy"),
            f"{command_id} cache policy",
        )
        _assert_environment_policy(
            admitted_policies,
            cache_policy=cache_policy,
            command_id=command_id,
            credential_class=credential_class,
            environment_classes=environment_classes,
            network_policy=network_policy,
        )
        if credential_class == "none":
            sensitive = tuple(
                name for name in environment_allowlist if _SENSITIVE_ENVIRONMENT_TOKEN.search(name)
            )
            if sensitive:
                raise ValueError(
                    f"{command_id} credential-free environment allowlist contains "
                    f"credential-shaped names: {', '.join(sensitive)}"
                )
        exit_policy = as_object(command.get("exitCodePolicy"), f"{command_id} exit policy")
        if exit_policy.get("kind") != "zero" or exit_policy.get("successCodes") != [0]:
            raise ValueError(f"{command_id} must use the zero exit-code policy")
        commands[command_id] = WitnessCommand(
            argv=argv,
            cache_policy=cache_policy,
            command_id=command_id,
            credential_class=credential_class,
            cwd=cwd,
            environment_allowlist=environment_allowlist,
            environment_classes=environment_classes,
            environment_inherit=environment_inherit,
            network_policy=network_policy,
            timeout_ms=timeout_ms,
        )
    return commands


def _execute_command(
    command: WitnessCommand,
    *,
    source_environment: Mapping[str, str],
) -> None:
    if current_managed_process() is not None and command.command_id in _MANAGED_ADAPTER_COMMANDS:
        grace = (
            5
            if command.command_id
            in {"python.package-check", "python.test", "python.coverage", "python.persistence-test"}
            else 3
        )
        with managed_process_invocation(
            command.argv[1:],
            timeout_seconds=command.timeout_ms / 1000,
            graceful_seconds=grace,
            pytest_participant=command.command_id == "documentation.graph-falsifiers",
        ) as invocation:
            result = spawn(
                command.argv[0],
                invocation.arguments,
                cwd=command.cwd,
                env=project_command_environment(command, source_environment),
                max_buffer=MAX_COMMAND_OUTPUT_BYTES,
                timeout_seconds=command.timeout_ms / 1000,
                inherited_fds=invocation.inherited_fds,
                graceful_seconds=grace,
                cancellation_fd=invocation.cancellation_fd,
            )
    else:
        result = spawn(
            command.argv[0],
            command.argv[1:],
            cwd=command.cwd,
            env=project_command_environment(command, source_environment),
            max_buffer=MAX_COMMAND_OUTPUT_BYTES,
            timeout_seconds=command.timeout_ms / 1000,
        )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.error is not None:
        raise RuntimeError(f"witness command {command.command_id} failed: {result.error}")
    if result.status != 0:
        raise RuntimeError(
            f"witness command {command.command_id} failed with status {result.status}"
        )
    if current_managed_process() is not None and result.process_group_quiescent is not True:
        raise RuntimeError(f"witness command {command.command_id} has no owned-group drain")


def _admitted_environment_policies(
    witness_plan: Mapping[str, object],
) -> frozenset[tuple[str, str, str, str]]:
    vocabulary = as_object(witness_plan.get("vocabulary"), "witness vocabulary")
    policies: set[tuple[str, str, str, str]] = set()
    for raw_policy in as_array(
        vocabulary.get("environmentClassPolicies"),
        "environment class policies",
    ):
        policy = as_object(raw_policy, "environment class policy")
        environment_class = _nonempty_string(
            policy.get("environmentClass"),
            "environment class policy id",
        )
        cache_policies = _ordered_unique_strings(
            policy.get("cachePolicies"),
            f"{environment_class} cache policies",
        )
        credential_classes = _ordered_unique_strings(
            policy.get("credentialClasses"),
            f"{environment_class} credential classes",
        )
        network_policies = _ordered_unique_strings(
            policy.get("networkPolicies"),
            f"{environment_class} network policies",
        )
        policies.update(
            (environment_class, credential_class, network_policy, cache_policy)
            for credential_class in credential_classes
            for network_policy in network_policies
            for cache_policy in cache_policies
        )
    if not policies:
        raise ValueError("witness vocabulary must declare environment class policies")
    return frozenset(policies)


def _assert_environment_policy(
    admitted_policies: frozenset[tuple[str, str, str, str]],
    *,
    cache_policy: str,
    command_id: str,
    credential_class: str,
    environment_classes: tuple[str, ...],
    network_policy: str,
) -> None:
    invalid = tuple(
        environment_class
        for environment_class in environment_classes
        if (
            environment_class,
            credential_class,
            network_policy,
            cache_policy,
        )
        not in admitted_policies
    )
    if invalid:
        raise ValueError(
            f"{command_id} policy is not admitted for environment classes: " + ", ".join(invalid)
        )


def _environment_allowlist(value: object, command_id: str) -> tuple[str, ...]:
    names = _ordered_strings(value, f"{command_id} environment allowlist")
    if len(set(names)) != len(names):
        raise ValueError(f"{command_id} environment allowlist must not contain duplicates")
    invalid = tuple(name for name in names if _ENVIRONMENT_NAME.fullmatch(name) is None)
    if invalid:
        raise ValueError(
            f"{command_id} environment allowlist contains invalid names: " + ", ".join(invalid)
        )
    return names


def _ordered_unique_strings(value: object, context: str) -> tuple[str, ...]:
    strings = _ordered_strings(value, context)
    if len(set(strings)) != len(strings):
        raise ValueError(f"{context} must not contain duplicates")
    if not strings:
        raise ValueError(f"{context} must not be empty")
    return strings


def _ordered_strings(value: object, context: str) -> tuple[str, ...]:
    raw = as_array(value, context)
    if any(not isinstance(item, str) or not item or item != item.strip() for item in raw):
        raise ValueError(f"{context} must contain canonical non-empty strings")
    return tuple(item for item in raw if isinstance(item, str))


def _nonempty_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{context} must be canonical non-empty text")
    return value


def _exact_keys(value: Mapping[str, object], expected: Sequence[str], context: str) -> None:
    if set(value) != set(expected):
        raise ValueError(f"{context} fields are not canonical")


if __name__ == "__main__":
    from scripts.dev_environment.environment import managed_process_entrypoint

    raise SystemExit(managed_process_entrypoint(main))
