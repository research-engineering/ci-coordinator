"""Orchestration and closed-input controls; native Docker evidence is separate."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from scripts import build_input_witness as witness
from scripts.bounded_process import CommandResult

ROOT = Path(__file__).resolve().parents[2]
PIN = (
    "pnpm@12.5.1+sha512."
    "e3f305bc784a2bc89f5ad3b6138889470fae8d2af5f36b61216ec91c2c3d64089775f"
    "9de38aac331044ea40f245cb0d5666392dfdf65824e1907ef6a2c62de5f"
)


def test_build_input_catalog_and_matcher_are_exact() -> None:
    catalog = json.loads((ROOT / "proofkit/witness-plan-input.json").read_text())
    commands = [command for command in catalog["commands"] if command["id"] == "build.inputs"]
    argv = ["backend/.venv/bin/python", "-m", "scripts.build_input_witness"]

    assert commands == [
        {
            "argv": argv,
            "cachePolicy": "write-local",
            "credentialClass": "none",
            "cwd": ".",
            "environment": {
                "allowlist": ["PATH"],
                "classes": ["local-python-container-external"],
                "inherit": "allowlist",
            },
            "exitCodePolicy": {"kind": "zero", "successCodes": [0]},
            "expectedArtifacts": [],
            "id": "build.inputs",
            "networkPolicy": "external",
            "parallelGroup": "build",
            "schemaVersion": 1,
            "timeoutMs": 600_000,
        }
    ]
    profile = json.loads((ROOT / "proofkit/repo-profile.json").read_text())
    matchers = profile["commandMatchers"]
    matcher_ids = [matcher["id"] for matcher in matchers]
    assert matcher_ids == sorted(set(matcher_ids))
    assert [
        matcher for matcher in matchers if matcher["id"] == "ci-coordinator.command.build-inputs"
    ] == [
        {
            "allowedArgv": argv,
            "credentialClass": "none",
            "id": "ci-coordinator.command.build-inputs",
            "kind": "exact_argv",
            "networkPolicy": "external",
            "parallelGroup": "build",
        }
    ]
    matrix = json.loads((ROOT / "proofkit/ci-matrix.v1.json").read_text())
    rows = [row for row in matrix["commands"] if row["commandId"] == "build.inputs"]
    assert len(rows) == 1
    assert rows[0]["executionOwner"] == "proofkit/witness-plan-input.json#build.inputs"
    assert rows[0]["kind"] == "native"
    docker = next(surface for surface in matrix["surfaces"] if surface["id"] == "docker")
    assert docker["commandIds"].count("build.inputs") == 1
    assert "container.smoke" in docker["commandIds"]


def test_native_job_runs_bounded_build_inputs_before_unchanged_smoke() -> None:
    workflow = YAML(typ="safe").load(
        (ROOT / ".github/workflows/python-persistence.yml").read_text()
    )
    job = workflow["jobs"]["container-runtime-smoke"]

    assert set(job) == {"name", "runs-on", "timeout-minutes", "steps"}
    assert job["runs-on"] == "ubuntu-26.04-arm"
    assert job["timeout-minutes"] == 25
    steps = job["steps"]
    assert len(steps) == 5
    assert steps[1]["name"] == "Set up Python"
    assert steps[2]["uses"] == ("docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1")
    assert steps[2]["with"] == {
        "image": "tonistiigi/binfmt@sha256:"
        "400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0",
        "platforms": "amd64",
        "cache-image": False,
    }
    assert steps[3:] == [
        {
            "name": "Qualify build inputs",
            "timeout-minutes": 10,
            "run": "python3 -m scripts.build_input_witness",
        },
        {
            "name": "Build and verify the runtime image",
            "run": "python3 -m scripts.container_runtime_smoke",
        },
    ]


@pytest.mark.parametrize(
    ("path", "kind", "commands"),
    [
        (".dockerignore", "technical", ("build.inputs",)),
        ("Dockerfile", "technical", ("build.inputs", "container.smoke")),
        ("docker/ci/connected-browser.Dockerfile", "technical", ("build.inputs",)),
        (
            "docs/features/build-input-admission.md",
            "contract",
            ("documentation.graph", "requirements.admission"),
        ),
        ("frontend/Dockerfile.dev", "technical", ("build.inputs",)),
        ("package.json", "contract", ("build.inputs", "repository.json")),
        (
            "scripts/build_input_witness.py",
            "technical",
            ("build.inputs", "python.lint", "python.test", "python.typecheck"),
        ),
        (
            "scripts/dev_environment/toolchain.py",
            "technical",
            ("build.inputs", "python.lint", "python.test", "python.typecheck"),
        ),
        (
            "scripts/tests/test_build_input_witness.py",
            "falsification",
            ("python.lint", "python.test", "python.typecheck"),
        ),
    ],
)
def test_build_input_routes_and_required_owner_commands(
    path: str, kind: str, commands: tuple[str, ...]
) -> None:
    routes = json.loads((ROOT / "proofkit/routes/runtime.v2.json").read_text())
    rows = [row for row in routes["bindings"] if row[0] == "010" and row[4] == path]
    expected_commands = sorted((*commands, "proofkit.verify", "selective.plan", "text.policy"))

    assert len(rows) == 1
    assert rows[0][3] == kind
    assert rows[0][5] == expected_commands
    assert rows[0][6] == (
        ["local-python", "local-python-container-external"]
        if "build.inputs" in commands
        else ["local-python"]
    )
    required = json.loads((ROOT / "proofkit/required-binding-tuples.v1.json").read_text())
    assert [
        row
        for row in required["owners"]
        if row["requirementId"] == "REQ-CI-RUNTIME-010" and row["witnessPath"] == path
    ] == [
        {
            "requirementId": "REQ-CI-RUNTIME-010",
            "witnessPath": path,
            "requiredCommandIds": expected_commands,
        }
    ]


def test_native_entrypoint_imports_without_site_packages() -> None:
    result = subprocess.run(
        [sys.executable, "-S", "-B", "-c", "import scripts.build_input_witness"],
        cwd=ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


@pytest.mark.parametrize(
    "output",
    [
        "Corepack home must be fresh\n",
        "#7 0.105 Corepack home must be fresh\n",
        "#7 0.105 Internal Error: Corepack home must be fresh\n",
    ],
)
def test_failure_requires_an_emitted_diagnostic(output: str) -> None:
    assert witness._emitted_failure(CommandResult(1, "", output), "Corepack home must be fresh")


@pytest.mark.parametrize(
    "output",
    [
        '#7 [2/2] RUN mkdir "$COREPACK_HOME" || { echo "Corepack home must be fresh"; exit 1; }\n',
        'Dockerfile:5\n>>> RUN echo "Corepack home must be fresh"\nnetwork unreachable\n',
        "network unreachable\n",
        "prefix Corepack home must be fresh suffix\n",
    ],
)
def test_docker_echo_and_unrelated_failures_do_not_qualify_a_guard(output: str) -> None:
    assert not witness._emitted_failure(CommandResult(1, "", output), "Corepack home must be fresh")


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(0, "Corepack home must be fresh\n", ""),
        CommandResult(1, "", "network failed"),
        CommandResult(1, "", "Corepack home must be fresh\n", failure_kind="timeout"),
        CommandResult(None, "", "Corepack home must be fresh\n"),
        CommandResult(None, "", "", error="spawn failed"),
    ],
)
def test_negative_requires_the_named_guard_and_clean_command_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: CommandResult
) -> None:
    monkeypatch.setattr(witness, "spawn", lambda *args, **kwargs: result)
    commands = witness._Commands(ROOT, tmp_path)
    with pytest.raises(RuntimeError):
        commands.execute("docker", ("build", "."), failure="Corepack home must be fresh")


def test_command_budget_and_environment_are_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[dict[str, object]] = []

    def capture(command: str, args: Sequence[str], **options: object) -> CommandResult:
        observed.append(options)
        return CommandResult(0, "", "")

    monkeypatch.setenv("GITHUB_TOKEN", "must-not-be-inherited")
    monkeypatch.setenv("COREPACK_INTEGRITY_KEYS", "0")
    monkeypatch.setattr(witness, "spawn", capture)
    commands = witness._Commands(ROOT, tmp_path)
    commands.execute("docker", ("version",))

    assert observed[0]["env"] == {
        "PATH": commands.environment["PATH"],
        "HOME": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    assert observed[0]["timeout_seconds"] == 180
    assert observed[0]["max_buffer"] == 2 * 1024 * 1024
    commands.deadline = 0
    with pytest.raises(TimeoutError, match="finite budget"):
        commands.execute("docker", ("version",))
    assert len(observed) == 1


@pytest.mark.parametrize("failure", ["permission denied", "denied: No such image: owned"])
def test_cleanup_uncertainty_cannot_be_a_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(witness, "spawn", lambda *args, **kwargs: CommandResult(1, "", failure))
    commands = witness._Commands(ROOT, tmp_path)
    commands.images.append("owned")
    with pytest.raises(RuntimeError, match="cleanup"):
        commands.cleanup()


def test_cleanup_visits_only_recorded_names_even_after_one_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def capture(command: str, args: Sequence[str], **options: object) -> CommandResult:
        calls.append(tuple(args))
        if args[0] == "container":
            return CommandResult(1, "", "permission denied")
        return CommandResult(1, "", "Error response from daemon: No such image: owned-image\n")

    monkeypatch.setattr(witness, "spawn", capture)
    commands = witness._Commands(ROOT, tmp_path)
    commands.containers.append("owned-container")
    commands.images.append("owned-image")
    with pytest.raises(RuntimeError, match="cleanup"):
        commands.cleanup()
    assert calls == [
        ("container", "rm", "--force", "owned-container"),
        ("image", "rm", "--force", "owned-image"),
    ]


def test_native_plan_uses_actual_three_recipes_and_single_cause_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None]] = []
    commands = witness._Commands(ROOT, tmp_path)

    def record(context: Path, recipe: str, *, failure: str | None = None) -> str:
        calls.append((recipe, failure))
        return f"owned-{len(calls)}"

    monkeypatch.setattr(commands, "build", record)
    result = witness._corepack_checks(commands, ROOT, tmp_path)

    assert result["recipes"] == [
        "Dockerfile",
        "frontend/Dockerfile.dev",
        "docker/ci/connected-browser.Dockerfile",
    ]
    assert result["manager"] == PIN
    assert len(calls) == 10
    for recipe, failure in calls[:3]:
        assert failure is None
        assert "FROM node:24.21.0-trixie-slim@sha256:" in recipe
        assert f"corepack prepare {PIN} --activate" in recipe
        assert 'test "$(pnpm --version)" = 12.5.1' in recipe
        assert "npm install" not in recipe
        assert "COREPACK_INTEGRITY_KEYS" not in recipe
    expected = PIN.split("+sha512.")[1]
    alternate = "0" + expected[1:]
    assert calls[3][1] == f"Mismatch hashes. Expected {alternate}, got {expected}"
    assert calls[3][0] == calls[0][0].replace(PIN, "pnpm@12.5.1+sha512." + alternate).removesuffix(
        'RUN test "$(pnpm --version)" = 12.5.1\n'
    )
    assert calls[4][1] == calls[5][1] == "Bundled Corepack version unavailable"
    assert calls[6][1] == "Bundled Node version unavailable"
    assert calls[7][1] == calls[9][1] == "Corepack home must be fresh"
    assert calls[7][0].startswith("FROM owned-1\n")
    assert calls[8][0] == calls[7][0].replace('mkdir "$COREPACK_HOME"', 'mkdir -p "$COREPACK_HOME"')
    assert calls[8][1] is None


def test_stage_reader_reuses_native_owner_but_rejects_new_copy_mechanism(tmp_path: Path) -> None:
    source = (ROOT / "frontend/Dockerfile.dev").read_text()
    target = tmp_path / "frontend/Dockerfile.dev"
    target.parent.mkdir()
    target.write_text(source)
    prefix, command = witness._node_stage(tmp_path, "frontend/Dockerfile.dev", PIN)
    assert "WORKDIR /workspace" in prefix
    assert "chown -R node:node" in command
    target.write_text(source.replace("RUN test", "COPY package.json ./\nRUN test", 1))
    with pytest.raises(RuntimeError, match="literal shape"):
        witness._node_stage(tmp_path, "frontend/Dockerfile.dev", PIN)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("../escape", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("linked", tarfile.SYMTYPE),
        ("hard", tarfile.LNKTYPE),
    ],
)
def test_snapshot_members_cannot_escape_or_link(name: str, kind: bytes) -> None:
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = "target" if kind != tarfile.REGTYPE else ""
        archive.addfile(info)
    content.seek(0)
    with (
        tarfile.open(fileobj=content) as archive,
        pytest.raises(RuntimeError, match="member boundary"),
    ):
        witness._source_members(archive)


def test_snapshot_member_positive_and_independent_count_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        archive.addfile(tarfile.TarInfo("required/resource.json"), io.BytesIO())
    for limit in (1, 0):
        content.seek(0)
        monkeypatch.setattr(witness, "_MAX_MEMBERS", limit)
        with tarfile.open(fileobj=content) as archive:
            if limit:
                assert [item.name for item in witness._source_members(archive)] == [
                    "required/resource.json"
                ]
            else:
                with pytest.raises(RuntimeError):
                    witness._source_members(archive)


def test_snapshot_total_bytes_are_bounded_independently_of_member_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        member = tarfile.TarInfo("required/resource.json")
        member.size = 2
        archive.addfile(member, io.BytesIO(b"{}"))
    content.seek(0)
    monkeypatch.setattr(witness, "_MAX_SOURCE_BYTES", 1)
    with (
        tarfile.open(fileobj=content) as archive,
        pytest.raises(RuntimeError, match="member boundary"),
    ):
        witness._source_members(archive)


def test_required_context_resources_are_literal_positive_operands() -> None:
    required = witness._required_files(ROOT)
    assert {
        "LICENSE",
        "NOTICE",
        "frontend/index.html",
        "frontend/dev/production.vite.config.ts",
        "patches/minimatch@5.1.9.patch",
        "frontend/src/assets/fonts/OFL.txt",
        "backend/alembic.ini",
        "backend/uv.lock",
        "backend/alembic/versions/20260926_0017_explicit_review_renewal.py",
        "backend/src/ci_coordinator/runtime_settings/resources/build-identity.v1.json",
    } <= required.keys()
    assert len([path for path in required if path.endswith(".woff2")]) == 4


def test_qualification_rejects_a_foreign_root_before_commands(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="exact repository root"):
        witness.qualify_build_inputs(tmp_path)


@pytest.mark.parametrize("remaining", [3.0, 2.0, 0.0])
def test_work_does_not_consume_reserved_termination_or_cleanup_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remaining: float
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    commands = witness._Commands(ROOT, tmp_path, deadline=100.0 + 60.0 + remaining)
    monkeypatch.setattr(witness, "spawn", lambda *a, **k: pytest.fail("reserve spent on work"))
    with pytest.raises(TimeoutError):
        commands.execute("docker", ("version",))


def test_remaining_work_budget_subtracts_cleanup_and_child_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    observed: list[object] = []

    def capture(*args: object, **options: object) -> CommandResult:
        observed.append(options["timeout_seconds"])
        return CommandResult(0, "", "")

    monkeypatch.setattr(witness, "spawn", capture)
    commands = witness._Commands(ROOT, tmp_path, deadline=170.0)
    commands.execute("docker", ("version",))
    assert observed == [7.0]


@pytest.mark.parametrize("status", [0, 1])
def test_late_positive_or_named_negative_cannot_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def late(*args: object, **options: object) -> CommandResult:
        clock[0] = 510.0
        return CommandResult(status, "Corepack home must be fresh\n", "")

    monkeypatch.setattr(witness, "spawn", late)
    commands = witness._Commands(ROOT, tmp_path)
    with pytest.raises(TimeoutError, match="late"):
        commands.execute(
            "docker", ("build", "."), failure=None if status == 0 else "Corepack home must be fresh"
        )


@pytest.fixture
def lifecycle_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, tuple[str, ...], float]]:
    """Rest-valid lifecycle operands, not native carrier/context qualification."""
    calls: list[tuple[str, tuple[str, ...], float]] = []

    def completed(command: str, args: Sequence[str], **options: object) -> CommandResult:
        calls.append((command, tuple(args), float(str(options["timeout_seconds"]))))
        output = "a" * 40 if tuple(args) == ("rev-parse", "HEAD") else ""
        return CommandResult(0, output, "")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(witness, "spawn", completed)
    monkeypatch.setattr(witness, "_snapshot", lambda *args: "a" * 40)
    monkeypatch.setattr(witness, "_corepack_checks", lambda *args: {"fixture": "lifecycle_only"})
    monkeypatch.setattr(witness, "_context_checks", lambda *args: {"fixture": "lifecycle_only"})
    return calls


@pytest.mark.parametrize("kind", [ValueError, KeyboardInterrupt])
def test_primary_and_cancellation_survive_cleanup_failure(
    lifecycle_only: list[tuple[str, tuple[str, ...], float]],
    monkeypatch: pytest.MonkeyPatch,
    kind: type[BaseException],
) -> None:
    primary = kind("primary")
    cleanup_calls: list[Path] = []

    def fail_body(*args: object) -> None:
        raise primary

    def fail_cleanup(self: witness._Commands) -> None:
        cleanup_calls.append(self.home)
        raise OSError("private secondary message must not be retained")

    monkeypatch.setattr(witness, "_corepack_checks", fail_body)
    monkeypatch.setattr(witness._Commands, "cleanup", fail_cleanup)
    with pytest.raises(kind) as captured:
        witness.qualify_build_inputs(ROOT)
    assert captured.value is primary
    assert primary.__notes__ == ["build-input cleanup uncertain: OSError"]
    assert len(cleanup_calls) == 1


def test_late_host_cleanup_success_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    commands = witness._Commands(ROOT, tmp_path / "owned")
    commands.create_home()

    def late(*args: object, **kwargs: object) -> CommandResult:
        clock[0] = 20.0
        return CommandResult(0, "", "")

    monkeypatch.setattr(witness, "spawn", late)
    with pytest.raises(TimeoutError, match="late"):
        commands.cleanup()


def test_docker_cleanup_cannot_spend_host_cleanup_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    commands = witness._Commands(ROOT, tmp_path / "owned")
    commands.create_home()
    commands.images.append("owned-image")
    calls: list[tuple[str, object]] = []
    clock[0] = 545.0

    def completed(command: str, args: Sequence[str], **options: object) -> CommandResult:
        calls.append((command, options["timeout_seconds"]))
        return CommandResult(0, "", "")

    monkeypatch.setattr(witness, "spawn", completed)
    with pytest.raises(TimeoutError, match="reserve"):
        commands.cleanup()
    assert calls == [(sys.executable, 17.0)]


@pytest.mark.parametrize("late_at", [None, "body", "output"])
def test_cli_receipt_requires_timely_successful_body_and_publication(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    late_at: str | None,
) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def complete(root: Path) -> dict[str, object]:
        if late_at == "body":
            clock[0] = 570.0
        return {"fixture": "lifecycle_only"}

    class Output:
        def write(self, value: str) -> int:
            clock[0] = 570.0
            return len(value)

    monkeypatch.setattr(witness, "qualify_build_inputs", complete)
    with monkeypatch.context() as output:
        if late_at == "output":
            output.setattr(sys, "stdout", Output())
        result = witness.main()
    assert result == (0 if late_at is None else 1)
    captured = capsys.readouterr()
    if late_at is None:
        assert captured.out == '{"fixture": "lifecycle_only"}\n'
        assert captured.err == ""
    else:
        assert captured.out == ""
        assert "completion was late" in captured.err


def test_isolated_context_failure_never_attempts_ignore_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "source"
    snapshot.mkdir()
    ignore = snapshot / ".dockerignore"
    original = "\n".join(witness._CONTEXT_MARKERS) + "\n"
    ignore.write_text(original)
    commands = witness._Commands(ROOT, tmp_path)
    primary = OSError("context control failed")
    calls = [0]

    def copy_context(source: Path, destination: Path) -> None:
        calls[0] += 1
        if calls[0] == 2:
            raise primary
        destination.mkdir()

    monkeypatch.setattr(witness, "_required_files", lambda root: {})
    monkeypatch.setattr(commands, "context_files", copy_context)
    with pytest.raises(OSError) as captured:
        witness._context_checks(commands, snapshot)
    assert captured.value is primary
    assert ignore.read_text() == original.replace("**/.env\n", "")
    assert (tmp_path / "context-positive").is_dir()


def test_successful_body_with_uncertain_cleanup_has_no_receipt(
    lifecycle_only: list[tuple[str, tuple[str, ...], float]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_cleanup(self: witness._Commands) -> None:
        raise RuntimeError("cleanup unconfirmed")

    monkeypatch.setattr(witness._Commands, "cleanup", fail_cleanup)
    with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
        witness.qualify_build_inputs(ROOT)


def test_lifecycle_positive_uses_owned_host_child_inside_original_budget(
    lifecycle_only: list[tuple[str, tuple[str, ...], float]],
) -> None:
    result = witness.qualify_build_inputs(ROOT)
    assert result["sourceCommit"] == "a" * 40
    command, args, timeout = lifecycle_only[-1]
    assert command == sys.executable
    assert args[:3] == ("-I", "-B", "-c")
    assert args[3] == witness._HOST_CLEANUP
    assert Path(args[4]).name.startswith("ci-build-input-")
    assert timeout == 17.0


@pytest.mark.parametrize("late_phase", ["work", "cleanup"])
def test_phase_success_returned_after_its_deadline_is_not_a_receipt(
    lifecycle_only: list[tuple[str, tuple[str, ...], float]],
    monkeypatch: pytest.MonkeyPatch,
    late_phase: str,
) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def late_work(*args: object) -> dict[str, str]:
        clock[0] = 510.0
        return {"fixture": "late"}

    def late_cleanup(self: witness._Commands) -> None:
        clock[0] = 570.0

    if late_phase == "work":
        monkeypatch.setattr(witness, "_context_checks", late_work)
    else:
        monkeypatch.setattr(witness._Commands, "cleanup", late_cleanup)
    with pytest.raises(TimeoutError):
        witness.qualify_build_inputs(ROOT)


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(None, "", "", failure_kind="timeout"),
        CommandResult(None, "", "", error="spawn unavailable"),
        CommandResult(1, "", "permission denied"),
    ],
)
def test_filesystem_cleanup_unknown_is_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: CommandResult
) -> None:
    commands = witness._Commands(ROOT, tmp_path / "owned")
    commands.create_home()
    monkeypatch.setattr(witness, "spawn", lambda *args, **kwargs: result)
    with pytest.raises(RuntimeError, match=r"cleanup|complete"):
        commands.cleanup()
    assert commands.home.is_dir()


def test_cleanup_continues_to_host_after_docker_cancellation_and_retains_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = witness._Commands(ROOT, tmp_path / "owned")
    commands.create_home()
    commands.images.append("owned-image")
    primary = KeyboardInterrupt("cancel Docker cleanup")
    calls: list[str] = []

    def capture(command: str, args: Sequence[str], **options: object) -> CommandResult:
        calls.append(command)
        if command == commands.docker:
            raise primary
        return CommandResult(1, "", "private host error")

    monkeypatch.setattr(witness, "spawn", capture)
    with pytest.raises(KeyboardInterrupt) as captured:
        commands.cleanup()
    assert captured.value is primary
    assert calls == [commands.docker, sys.executable]
    assert primary.__notes__ == ["build-input cleanup uncertain: RuntimeError"]


def test_collision_is_not_owned_and_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = witness._Commands(ROOT, tmp_path)
    (tmp_path / "retained").write_text("foreign")
    with pytest.raises(FileExistsError):
        commands.create_home()
    monkeypatch.setattr(witness, "spawn", lambda *args, **kwargs: pytest.fail("foreign cleanup"))
    commands.cleanup()
    assert (tmp_path / "retained").read_text() == "foreign"


def test_real_stdlib_host_cleanup_child_removes_only_owned_root(tmp_path: Path) -> None:
    commands = witness._Commands(ROOT, tmp_path / "owned")
    commands.create_home()
    (commands.home / "nested").mkdir()
    (commands.home / "nested" / "fixture").write_text("owned")
    sibling = tmp_path / "retained"
    sibling.write_text("foreign")
    commands.cleanup()
    assert not commands.home.exists()
    assert sibling.read_text() == "foreign"


def test_real_host_cleanup_rejects_replaced_root_symlink(tmp_path: Path) -> None:
    commands = witness._Commands(ROOT, tmp_path / "owned")
    commands.create_home()
    commands.home.rmdir()
    sibling = tmp_path / "retained"
    sibling.mkdir()
    (sibling / "fixture").write_text("foreign")
    commands.home.symlink_to(sibling, target_is_directory=True)
    with pytest.raises(RuntimeError, match="cleanup"):
        commands.cleanup()
    assert (sibling / "fixture").read_text() == "foreign"


def test_cli_reports_primary_and_class_only_secondary_without_smoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    primary = ValueError("original guard")
    primary.add_note("build-input cleanup uncertain: OSError")

    def reject(root: Path) -> dict[str, object]:
        raise primary

    monkeypatch.setattr(witness, "qualify_build_inputs", reject)
    assert witness.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "build-input qualification failed: original guard\nbuild-input cleanup uncertain: OSError\n"
    )


def test_cli_does_not_convert_cancellation_to_ordinary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = KeyboardInterrupt("cancel")

    def cancel(root: Path) -> dict[str, object]:
        raise primary

    monkeypatch.setattr(witness, "qualify_build_inputs", cancel)
    with pytest.raises(KeyboardInterrupt) as captured:
        witness.main()
    assert captured.value is primary
