from __future__ import annotations

import fnmatch
import json
import re
import shlex
import tomllib
from pathlib import Path

from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[2]
EXACT_SEMVER = re.compile(r"\d+\.\d+\.\d+")
MISE_CALVER = re.compile(r"\d{4}\.\d+\.\d+")
SHA256 = re.compile(r"[0-9a-f]{64}")


def test_mise_is_the_locked_cross_language_task_owner() -> None:
    with (REPO_ROOT / "mise.toml").open("rb") as source:
        config = tomllib.load(source)

    assert MISE_CALVER.fullmatch(config["min_version"])
    assert config["settings"] == {"activate_aggressive": True, "auto_install": False}
    assert set(config["tools"]) == {"gitleaks", "node", "pnpm", "python", "uv"}
    assert all(EXACT_SEMVER.fullmatch(version) for version in config["tools"].values())
    assert config["tasks"]["install"]["run"] == [
        "mise install --locked python uv node pnpm gitleaks",
        "mise exec -- sh -c 'scanner_version=$(gitleaks version) && "
        'test "$scanner_version" = "8.30.1"\'',
        "mise exec -- python -S -m scripts.dev_environment.task install",
    ]
    assert config["tasks"]["install:backend"]["run"] == [
        "mise install --locked python uv",
        "mise exec -- python -S -m scripts.dev_environment.task install:backend",
    ]
    assert config["tasks"]["install:frontend"]["run"] == [
        "mise install --locked python node pnpm",
        "mise exec -- python -S -m scripts.dev_environment.task install:frontend",
    ]
    assert config["tasks"]["dev:watch"]["raw"] is True
    for task_name in ("dev:watch", "check:portable", "browser:install", "dev:debug-backend"):
        assert config["tasks"][task_name]["run"] == (
            f"python -S -m scripts.dev_environment.task {task_name}"
        )
    for task_name in ("dev:prepare", "dev:up", "dev:watch"):
        assert config["tasks"][task_name]["depends"] == ["install:backend"]
    assert "depends" not in config["tasks"]["check"]
    assert "depends" not in config["tasks"]["check:portable"]
    assert config["tasks"]["dev:debug-backend"]["raw"] is True

    with (REPO_ROOT / "mise.lock").open("rb") as source:
        locked = tomllib.load(source)["tools"]
    assert {
        key.removeprefix("platforms.") for key in locked["node"][0] if key.startswith("platforms.")
    } == {
        "linux-arm64",
        "linux-x64",
        "macos-arm64",
        "macos-x64",
    }
    for tool in ("pnpm", "gitleaks"):
        assert {
            key.removeprefix("platforms.")
            for key in locked[tool][0]
            if key.startswith("platforms.")
        } == {"linux-arm64", "linux-x64", "macos-arm64"}
    for tool in ("python", "uv"):
        assert {
            key.removeprefix("platforms.")
            for key in locked[tool][0]
            if key.startswith("platforms.")
        } == {"linux-arm64", "linux-x64", "macos-arm64", "macos-x64"}


def test_pnpm_separates_dependency_installation_from_proof_commands() -> None:
    workspace = YAML(typ="safe").load(REPO_ROOT / "pnpm-workspace.yaml")

    assert workspace["minimumReleaseAge"] == 1_440
    assert workspace["engineStrict"] is True
    assert workspace["storeDir"] == ".pnpm-store"
    assert workspace["allowBuilds"] == {"esbuild": True}
    assert workspace["enableGlobalVirtualStore"] is False
    assert workspace["verifyDepsBeforeRun"] == "error"
    assert "onlyBuiltDependencies" not in workspace


def test_ty_evaluation_ledger_is_complete_and_disjoint() -> None:
    ledger = json.loads((REPO_ROOT / "docs/decisions/ty-0.0.61-evaluation.v1.json").read_text())
    expected_counts = {
        "callable-contract": 2,
        "collection-element-narrowing": 7,
        "dynamic-json-container-narrowing": 47,
        "generic-exact-type-narrowing": 1,
        "literal-membership-narrowing": 9,
        "nested-generic-inference": 1,
        "nested-log-mapping": 1,
        "positional-only-stream-protocol": 6,
        "postgresql-row-shape": 7,
        "relational-optional-narrowing": 7,
        "sql-keyword-provenance": 2,
        "sqlalchemy-select-typing": 1,
        "strict-mypy-required-casts": 5,
        "workflow-evidence-category": 2,
        "yaml-impossible-key-state": 1,
    }
    expected_disposition_counts = {
        "change-proof-bridge": 47,
        "change-static-contract": 21,
        "retain-checker-limitation": 25,
        "retain-dependency-gap": 1,
        "retain-mypy-contract": 5,
    }
    classes = {item["id"]: item for item in ledger["classes"]}
    fingerprints = [
        fingerprint for item in ledger["classes"] for fingerprint in item["baselineFingerprints"]
    ]

    assert ledger["schemaVersion"] == "ci-coordinator-ty-evaluation/v1"
    assert {name: len(item["baselineFingerprints"]) for name, item in classes.items()} == (
        expected_counts
    )
    assert {
        disposition: sum(
            len(item["baselineFingerprints"])
            for item in ledger["classes"]
            if item["disposition"] == disposition
        )
        for disposition in expected_disposition_counts
    } == expected_disposition_counts
    assert len(fingerprints) == len(set(fingerprints)) == 99
    assert (
        sum(
            len(item["baselineFingerprints"])
            for item in ledger["classes"]
            if item["disposition"].startswith("change-")
        )
        == 68
    )
    assert (
        sum(
            len(item["baselineFingerprints"])
            for item in ledger["classes"]
            if item["disposition"].startswith("retain-")
        )
        == 31
    )
    assert ledger["evaluation"]["baseline"]["diagnosticCount"] == 99
    assert ledger["evaluation"]["candidate"]["diagnosticCount"] == 31
    assert all(
        SHA256.fullmatch(report["reportSha256"])
        for report in (
            ledger["evaluation"]["baseline"],
            ledger["evaluation"]["candidate"],
        )
    )


def test_devcontainer_is_locked_and_has_no_host_docker_authority() -> None:
    config = json.loads((REPO_ROOT / ".devcontainer/devcontainer.json").read_text())
    dockerfile = (REPO_ROOT / ".devcontainer/Dockerfile").read_text()
    mise_config = (REPO_ROOT / "mise.toml").read_text()

    assert config["postCreateCommand"] == "bash .devcontainer/post-create.sh"
    assert "dockerComposeFile" not in config
    assert config["mounts"] == [
        (
            "source=${devcontainerId}-backend-venv,"
            "target=${containerWorkspaceFolder}/backend/.venv,type=volume"
        ),
        "source=${devcontainerId}-mise-data,target=/home/vscode/.local/share/mise,type=volume",
        (
            "source=${devcontainerId}-pnpm-store,"
            "target=${containerWorkspaceFolder}/.pnpm-store,type=volume"
        ),
        (
            "source=${devcontainerId}-root-node-modules,"
            "target=${containerWorkspaceFolder}/node_modules,type=volume"
        ),
        (
            "source=${devcontainerId}-frontend-node-modules,"
            "target=${containerWorkspaceFolder}/frontend/node_modules,type=volume"
        ),
    ]
    assert all("type=volume" in mount for mount in config["mounts"])
    assert all("${devcontainerId}" in mount for mount in config["mounts"])
    assert {mount.split("-", 1)[1].split(",", 1)[0] for mount in config["mounts"]} == {
        "backend-venv",
        "frontend-node-modules",
        "mise-data",
        "pnpm-store",
        "root-node-modules",
    }
    assert "/var/run/docker.sock" not in json.dumps(config, sort_keys=True)
    vscode = config["customizations"]["vscode"]
    assert "TypeScriptTeam.native-preview" in vscode["extensions"]
    assert vscode["settings"] == {
        "js/ts.experimental.useTsgo": True,
        "js/ts.tsdk.path": "./frontend/node_modules/@typescript/native",
    }
    assert re.search(r"^FROM .+@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)
    assert "sha256sum --check --strict" in dockerfile
    assert len(re.findall(r"mise_sha=([0-9a-f]{64})", dockerfile)) == 2

    minimum = re.search(r'^min_version = "([^"]+)"$', mise_config, re.MULTILINE)
    image_tool = re.search(r"^ARG MISE_VERSION=([^\s]+)$", dockerfile, re.MULTILINE)
    assert minimum is not None and image_tool is not None
    assert MISE_CALVER.fullmatch(minimum.group(1))
    assert minimum.group(1) == image_tool.group(1)
    assert all(SHA256.fullmatch(value) for value in re.findall(r"mise_sha=([^ ;]+)", dockerfile))

    post_create = (REPO_ROOT / ".devcontainer/post-create.sh").read_text()
    assert "CI=true mise run install" in post_create
    assert post_create.index("mise trust") < post_create.index("mise run install")


def test_project_development_images_drop_root_before_runtime() -> None:
    backend = (REPO_ROOT / "docker/development/backend.Dockerfile").read_text()
    frontend = (REPO_ROOT / "frontend/Dockerfile.dev").read_text()

    assert "COPY --chown=65532:65532 backend/src ./backend/src" in backend
    assert "COPY --chown=65532:65532 backend/alembic ./backend/alembic" in backend
    entrypoint = (REPO_ROOT / "docker/development/secret-entrypoint.sh").read_text()

    assert (
        'ENTRYPOINT ["/usr/local/bin/development-secret-entrypoint", "65532", "65532"]' in backend
    )
    assert "development-secret-entrypoint" not in frontend
    assert frontend.rstrip().splitlines()[-2] == "USER node"
    assert "--bounding-set=-all" in entrypoint
    assert "--no-new-privs" in entrypoint
    assert "install -m 0400" in entrypoint
    assert 'export HOME="$runtime_home"' in entrypoint
    source_copies = [
        line
        for line in frontend.splitlines()
        if line.startswith("COPY ") and "secret-entrypoint" not in line
    ]
    assert source_copies
    assert all(line.startswith("COPY --chown=node:node ") for line in source_copies)
    dependency_install = "RUN pnpm install --frozen-lockfile"
    assert frontend.index("USER node") < frontend.index(dependency_install)
    assert frontend.index(dependency_install) < frontend.index("USER root")


def test_runtime_services_copy_file_secrets_into_private_tmpfs() -> None:
    compose = YAML(typ="safe").load(REPO_ROOT / "compose.yaml")
    services = compose["services"]

    expected_secrets = {
        "migrate": {"migration-dsn"},
        "database-access": {"migration-dsn"},
        "backend": {
            "break-glass-bearer-token",
            "github-private-key.pem",
            "metrics-bearer-token",
            "plan-signing-private-key.pem",
            "runtime-dsn",
            "webhook-secret",
        },
    }
    for service, secrets in expected_secrets.items():
        assert services[service]["tmpfs"] == ["/run/ci-coordinator-secrets:mode=0700"]
        assert set(services[service]["secrets"]) == secrets
    assert "tmpfs" not in services["frontend"]
    assert "secrets" not in services["frontend"]
    assert services["frontend"]["user"] == "1000:1000"
    assert services["frontend"]["cap_drop"] == ["ALL"]
    assert services["frontend"]["security_opt"] == ["no-new-privileges:true"]

    backend_environment = services["backend"]["environment"]
    assert "CI_COORDINATOR_OUTBOUND_PROXY_URL" in backend_environment
    backend_command = services["backend"]["command"][-1]
    unset_block = (
        'if [ -z "$${CI_COORDINATOR_OUTBOUND_PROXY_URL:-}" ]; then\n'
        "  unset CI_COORDINATOR_OUTBOUND_PROXY_URL\n"
        "fi"
    )
    assert unset_block in backend_command
    assert backend_command.index(unset_block) < backend_command.index(
        "exec python -m ci_coordinator.runtime"
    )

    frontend_healthcheck = services["frontend"]["healthcheck"]["test"]
    assert frontend_healthcheck[:2] == ["CMD", "node"]
    assert (
        "fs.accessSync('/workspace/frontend/node_modules',fs.constants.W_OK)"
        in (frontend_healthcheck[-1])
    )


def test_database_provisioner_uses_native_file_input_without_password_arguments() -> None:
    compose = YAML(typ="safe").load(REPO_ROOT / "compose.yaml")
    provisioner = compose["services"]["database-provision"]
    command = provisioner["command"]
    bootstrap = (REPO_ROOT / "scripts/dev_environment/bootstrap_database.sql").read_text()

    assert command[:2] == ["/bin/sh", "-euc"]
    assert "exec psql --no-psqlrc --dbname postgres" in command[2]
    assert "--set" not in command[2]
    assert "migration_password" not in command[2]
    assert "runtime_password" not in command[2]
    assert "--file=/bootstrap/bootstrap_database.sql" in command[2]
    assert set(provisioner["secrets"]) == {
        "postgres-migration-password",
        "postgres-runtime-password",
        "postgres-superuser-password",
    }
    assert provisioner["volumes"] == [
        "./scripts/dev_environment/bootstrap_database.sql:/bootstrap/bootstrap_database.sql:ro"
    ]
    assert bootstrap.startswith("\\set ON_ERROR_STOP on\n")
    for role in ("migration", "runtime"):
        assert f"\\set {role}_password `cat /run/secrets/postgres-{role}-password`" in bootstrap
        assert f"\\set {role}_read_error :SHELL_ERROR" in bootstrap
        assert f"NOT :'{role}_read_error'::boolean" in bootstrap
        assert f"length(:'{role}_password') > 0" in bootstrap
    assert bootstrap.index("RAISE EXCEPTION") < bootstrap.index("CREATE ROLE")


def test_database_health_admission_uses_the_provisioner_transport() -> None:
    services = YAML(typ="safe").load(REPO_ROOT / "compose.yaml")["services"]
    assert services["postgres"]["healthcheck"]["test"] == [
        "CMD",
        "pg_isready",
        "--host=127.0.0.1",
        "--dbname=postgres",
        "--username=postgres",
    ]
    provisioner = services["database-provision"]
    assert provisioner["environment"]["PGHOST"] == "postgres"
    assert provisioner["depends_on"]["postgres"]["condition"] == "service_healthy"


def _copy_inputs(dockerfile: Path) -> set[Path]:
    inputs: set[Path] = {dockerfile, REPO_ROOT / ".dockerignore"}
    for line in dockerfile.read_text().splitlines():
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        words = [word for word in shlex.split(line)[1:] if not word.startswith("--")]
        for pattern in words[:-1]:
            matches = list(REPO_ROOT.glob(pattern))
            assert matches, pattern
            for match in matches:
                if match.is_dir():
                    inputs.update(path for path in match.rglob("*") if path.is_file())
                else:
                    inputs.add(match)
    return {
        path
        for path in inputs
        if "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and not any(part.endswith(".egg-info") for part in path.parts)
    }


def _watch_rules_for(path: Path, rules: list[dict[str, object]]) -> list[dict[str, object]]:
    matching = []
    for rule in rules:
        watch_path = REPO_ROOT / str(rule["path"])
        if path != watch_path and not path.is_relative_to(watch_path):
            continue
        relative = path.relative_to(watch_path).as_posix()
        ignored = rule.get("ignore", [])
        assert isinstance(ignored, list)
        if any(
            relative.startswith(pattern.rstrip("/") + "/") or fnmatch.fnmatchcase(relative, pattern)
            for pattern in ignored
        ):
            continue
        matching.append(rule)
    return matching


def test_every_material_development_copy_input_has_one_feedback_owner() -> None:
    services = YAML(typ="safe").load(REPO_ROOT / "compose.yaml")["services"]
    for service, dockerfile in (
        ("backend", REPO_ROOT / "docker/development/backend.Dockerfile"),
        ("frontend", REPO_ROOT / "frontend/Dockerfile.dev"),
    ):
        rules = services[service]["develop"]["watch"]
        assert {rule["action"] for rule in rules} <= {"sync", "sync+restart", "rebuild"}
        for path in _copy_inputs(dockerfile):
            matching = _watch_rules_for(path, rules)
            if path == REPO_ROOT / "backend/alembic.ini" or path.is_relative_to(
                REPO_ROOT / "backend/alembic"
            ):
                assert not matching, "database changes require explicit migration admission"
            else:
                assert len(matching) == 1, (service, path.relative_to(REPO_ROOT), matching)
        for excluded in (
            "compose.yaml",
            "docker/development/compose.debug.yaml",
            ".dev/instance.env",
            "backend/.venv/bin/python",
            "frontend/node_modules/native.node",
            "frontend/dist/index.html",
            "scripts/dev_environment/bootstrap_database.sql",
        ):
            assert not _watch_rules_for(REPO_ROOT / excluded, rules), excluded


def test_native_vite_config_import_closure_requires_restart_without_overlapping_hmr() -> None:
    compose = YAML(typ="safe").load(REPO_ROOT / "compose.yaml")
    rules = compose["services"]["frontend"]["develop"]["watch"]
    pending = [REPO_ROOT / "frontend/vite.config.ts"]
    seen = set()
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        matches = _watch_rules_for(source, rules)
        assert len(matches) == 1 and matches[0]["action"] == "sync+restart", source
        pending.extend(
            (source.parent / relative).resolve(strict=True)
            for relative in re.findall(r'from\s+["\'](\.[^"\']+)["\']', source.read_text())
        )
    assert REPO_ROOT / "frontend/src/api/workbench/limits.ts" in seen
    for source_path in ("frontend/src/main.tsx", "frontend/index.html"):
        assert _watch_rules_for(REPO_ROOT / source_path, rules)[0]["action"] == "sync"


def test_package_build_inputs_rebuild_and_never_sync_host_dependencies() -> None:
    compose = YAML(typ="safe").load(REPO_ROOT / "compose.yaml")
    expected = {
        "backend": [
            "backend/pyproject.toml",
            "backend/uv.lock",
            "docker/development/backend.Dockerfile",
            "docker/development/secret-entrypoint.sh",
            ".dockerignore",
        ],
        "frontend": [
            "frontend/package.json",
            "package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "patches/minimatch@5.1.9.patch",
            "frontend/Dockerfile.dev",
            ".dockerignore",
        ],
    }
    for service, paths in expected.items():
        rules = compose["services"][service]["develop"]["watch"]
        for path in paths:
            matches = _watch_rules_for(REPO_ROOT / path, rules)
            assert len(matches) == 1 and matches[0]["action"] == "rebuild", path
    assert (
        "COPY --chown=node:node frontend ./frontend"
        not in (REPO_ROOT / "frontend/Dockerfile.dev").read_text()
    )
    assert (
        "COPY --chown=65532:65532 backend ./backend"
        not in (REPO_ROOT / "docker/development/backend.Dockerfile").read_text()
    )


def test_debugger_is_optional_and_debug_image_is_not_the_default_target() -> None:
    manifest = tomllib.loads((REPO_ROOT / "backend/pyproject.toml").read_text())
    lock = tomllib.loads((REPO_ROOT / "backend/uv.lock").read_text())
    # The native adapter endpoint-file contract must be requalified on an upgrade.
    assert manifest["dependency-groups"]["debug"] == ["debugpy==1.8.22"]
    assert all("debugpy" not in dependency for dependency in manifest["project"]["dependencies"])
    assert all("debugpy" not in dependency for dependency in manifest["dependency-groups"]["dev"])
    package = next(item for item in lock["package"] if item["name"] == "ci-coordinator-backend")
    assert package["dev-dependencies"]["debug"] == [{"name": "debugpy"}]
    assert all(item["name"] != "debugpy" for item in package["dependencies"])
    debugger = next(item for item in lock["package"] if item["name"] == "debugpy")
    assert debugger["version"] == "1.8.22"
    assert debugger["source"] == {"registry": "https://pypi.org/simple"}
    assert all(artifact["hash"].startswith("sha256:") for artifact in debugger["wheels"])
    dockerfile = (REPO_ROOT / "docker/development/backend.Dockerfile").read_text()
    normal, debug = dockerfile.split("FROM application AS debug\n")
    assert "--group debug" not in normal
    assert "--frozen --no-dev --group debug" in debug
    assert dockerfile.rstrip().endswith("FROM application AS development")
    release = (REPO_ROOT / "Dockerfile").read_text()
    assert "debugpy" not in release and "--group debug" not in release


def test_debug_override_changes_only_execution_and_owned_loopback_attach() -> None:
    ordinary = YAML(typ="safe").load(REPO_ROOT / "compose.yaml")
    override = YAML(typ="rt").load(REPO_ROOT / "docker/development/compose.debug.yaml")
    assert set(override["services"]) == {"backend"}
    debug = override["services"]["backend"]
    assert set(debug) == {"build", "command", "ports", "develop", "environment"}
    assert debug["environment"] == {
        "DEBUGPY_ADAPTER_ENDPOINTS": (
            "${CI_COORDINATOR_DEBUGPY_ENDPOINTS_FILE:"
            "?The instance CLI owns the debugger endpoint file}"
        )
    }
    assert debug["build"] == {"target": "debug"}
    assert debug["ports"] == ["127.0.0.1::5678"]
    assert len(debug["develop"]["watch"]) == 0
    assert str(debug["develop"]["watch"].tag) == "!reset"
    for service in ("backend", "migrate", "database-access"):
        assert ordinary["services"][service]["build"]["target"] == "development"
    assert ordinary["services"]["backend"]["ports"] == ["127.0.0.1::3000"]
    assert debug["command"][:2] == ordinary["services"]["backend"]["command"][:2]
    ordinary_command = ordinary["services"]["backend"]["command"][-1]
    debug_command = debug["command"][-1]
    assert ordinary_command.split("exec python", 1)[0] == debug_command.split("exec python", 1)[0]
    assert "--wait-for-client" in debug_command and "--configure-subProcess false" in debug_command
    assert "-m ci_coordinator.runtime" in debug_command
