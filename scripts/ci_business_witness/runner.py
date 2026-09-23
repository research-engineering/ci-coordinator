from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import FrameType

import psycopg

from scripts.bounded_process import spawn
from scripts.ci_business_witness.attached_logs import AttachedLogs, qualify_attached_logs
from scripts.ci_business_witness.consumers import (
    apply_fixture_ownership,
    expected_mounts,
    expected_user,
    prepare_runtime_directories,
)
from scripts.ci_business_witness.diagnostics import (
    browser_diagnostic,
    container_diagnostic,
    provider_diagnostic,
    readiness_diagnostic,
    startup_diagnostic,
)
from scripts.ci_business_witness.fixture import Fixture, prepare_fixture, write_private
from scripts.ci_business_witness.lifecycle import (
    CLEANUP_SECONDS,
    EXECUTION_SECONDS,
    LOCAL_DOCKER_HOST,
    WitnessBudget,
    admit_docker_environment,
    database_host,
    local_docker_arguments,
)
from scripts.ci_business_witness.oracle import (
    STAGES,
    WitnessFailure,
    admit_logs,
    require,
    verify_checkpoint,
)
from scripts.ci_business_witness.storage import VolatileWorkspace, admit_container_storage

ROOT = Path(__file__).resolve().parents[2]
MAX_OUTPUT = 4 * 1024 * 1024
_BROWSER_FAILURES = frozenset(
    {
        "fixture-unavailable",
        "fixture-invalid",
        "console-output-limit",
        "session-identity",
        "session-roles",
        "secure-session-cookie",
        "commit-response",
        "reload-replayed-command",
        "replay-response",
        "operation-conflict",
        "revision-conflict",
        "csrf-admission",
        "logout-response",
        "anonymous-admission",
        "canary-admission",
    }
)
_READY = """const urls=JSON.parse(process.argv[1]);
const end=Date.now()+Number(process.argv[2]);
let attempts=0,httpStatus=null,failure='transport';
function report(ready){
  console.log(JSON.stringify({schemaVersion:'connected-readiness/v1',
    ready,attempts,httpStatus,failure:ready?null:failure}));
}
function stop(){report(false);process.exit(1);}
while(true){
  if(Date.now()>=end||attempts>=512)stop();
  attempts++;
  try{
    for(const url of urls){
      const remaining=end-Date.now();
      if(remaining<=0)stop();
      httpStatus=null;failure='transport';
      const signal=AbortSignal.timeout(Math.min(3000,remaining));
      const r=await fetch(url,{redirect:'manual',signal});
      httpStatus=r.status;
      if(r.status!==200){failure='http-status';throw Error('not-ready');}
      await r.arrayBuffer();
    }
    report(true);break;
  }catch(error){
    const code=error?.cause?.code;
    if(['ENOTFOUND','EAI_AGAIN'].includes(code))failure='dns';
    else if(['ERR_TLS_CERT_ALTNAME_INVALID','UNABLE_TO_VERIFY_LEAF_SIGNATURE',
      'SELF_SIGNED_CERT_IN_CHAIN','DEPTH_ZERO_SELF_SIGNED_CERT',
      'CERT_HAS_EXPIRED','CERT_SIGNATURE_FAILURE'].includes(code))failure='tls';
    else if(['ECONNREFUSED','ECONNRESET','ENETUNREACH','EHOSTUNREACH'].includes(code))
      failure='connect';
    else if(['TimeoutError','AbortError'].includes(error?.name)||
      code==='UND_ERR_CONNECT_TIMEOUT')failure='timeout';
    if(Date.now()>=end)stop();
    await new Promise(r=>setTimeout(r,Math.max(0,Math.min(500,end-Date.now()))));
  }
}
"""


class Runner:
    def __init__(
        self,
        fixture: Fixture,
        project: str,
        budget: WitnessBudget,
        workspace: VolatileWorkspace | None = None,
    ) -> None:
        self.fixture = fixture
        self.project = project
        self.budget = budget
        self.workspace = workspace
        self.environment = {
            key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "TMPDIR"}
        }
        self.environment.update(
            {
                "BUSINESS_PROJECT": project,
                "BUSINESS_FIXTURE": str(fixture.directory),
                "BUSINESS_APP_IMAGE": f"{project}:app",
                "BUSINESS_BROWSER_IMAGE": f"{project}:browser",
            }
        )
        self.compose = (
            "compose",
            "--project-name",
            project,
            "--file",
            str(ROOT / "docker/ci/connected-administrator.compose.yaml"),
        )
        self.stage = "initialization"
        self.checkpoints: list[str] = []
        self.owned = False
        self.image_ids: dict[str, str] = {}
        self.browser_exit: int | None = None
        self.browser_failure: str | None = None
        self.browser_diagnostic: dict[str, object] = {}
        self.log_receipts: dict[str, dict[str, int | str]] = {}
        self.provider_failures: list[dict[str, object]] = []
        self.readiness_checks: list[dict[str, object]] = []
        self.service_states: dict[str, dict[str, object]] = {}
        self.backend_startup: dict[str, object] = {}
        self.attached = AttachedLogs(ROOT, self.environment, budget, maximum_bytes=MAX_OUTPUT)
        self._stream_services: set[str] = set()
        self._logs_terminal = False
        self.containers: dict[str, str] = {}
        self.service_images: dict[str, str] = {}
        self.log_transport: dict[str, object] = {}
        self._readiness_sequence = 0
        self.docker_versions: dict[str, str] = {}

    def volatile_workspace(self) -> VolatileWorkspace:
        if self.workspace is None:
            raise WitnessFailure("volatile-workspace-required")
        return self.workspace

    def command(
        self, arguments: tuple[str, ...], *, timeout: float = 180, readiness: bool = False
    ) -> str:
        if self._stream_services and not self._logs_terminal:
            self.attached.assert_running()
        result = spawn(
            "docker",
            local_docker_arguments(arguments),
            cwd=ROOT,
            env=self.environment,
            max_buffer=MAX_OUTPUT,
            timeout_seconds=self.budget.timeout(timeout),
            decode_errors="surrogateescape",
        )
        success = result.status == 0 and result.error is None and result.failure_kind is None
        if not success and len(self.provider_failures) < 16:
            self.provider_failures.append(provider_diagnostic(self.stage, arguments, result))
        readiness_result = readiness_diagnostic(result.stdout) if readiness else None
        if readiness_result is not None and len(self.readiness_checks) < 8:
            self.readiness_checks.append({"stage": self.stage, **readiness_result})
        self.budget.timeout(1)
        data = (result.stdout + result.stderr).encode("utf-8", "surrogateescape")
        if data:
            admit_logs(data, self.fixture.canaries)
        require(
            success,
            "provider-command",
        )
        if readiness_result is not None:
            require(readiness_result.get("ready") is True, "readiness-evidence")
        return result.stdout

    def compose_command(self, *arguments: str, timeout: float = 180) -> str:
        return self.command((*self.compose, *arguments), timeout=timeout)

    def assert_unallocated(self) -> None:
        socket = Path("/var/run/docker.sock")
        require(socket.is_socket() and not socket.is_symlink(), "local-docker-socket")
        security = json.loads(self.command(("info", "--format", "{{json .SecurityOptions}}")))
        require(
            isinstance(security, list)
            and all(isinstance(item, str) for item in security)
            and not any(
                item.split(",")[0] in {"name=rootless", "name=userns"} for item in security
            ),
            "volatile-docker-user-namespace",
        )
        for resource in ("container", "network", "volume"):
            listing = self.command(
                (
                    resource,
                    "ls",
                    *(("--all",) if resource == "container" else ()),
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={self.project}",
                )
            )
            require(not listing.strip(), "foreign-project")
        images = self.command(("image", "ls", "--quiet", "--filter", f"reference={self.project}:*"))
        require(not images.strip(), "foreign-image")
        self.owned = True
        versions = json.loads(self.command(("version", "--format", "{{json .}}")))
        for role in ("Client", "Server"):
            version = versions[role]["Version"]
            require(
                isinstance(version, str)
                and re.fullmatch(r"[0-9A-Za-z.+_-]{1,80}", version) is not None,
                "docker-version",
            )
            self.docker_versions[role.lower()] = version

    def configure(self) -> None:
        self.stage = "compose-admission"
        parsed = json.loads(self.compose_command("config", "--format", "json"))
        require(parsed["networks"]["isolated"]["internal"] is True, "network-not-internal")
        services = parsed["services"]
        require(
            set(services)
            == {
                "postgres",
                "database-provision",
                "migrate",
                "keycloak",
                "proxy",
                "backend",
                "browser",
            },
            "service-population",
        )
        for service, value in services.items():
            reference = value["image"]
            require(isinstance(reference, str) and bool(reference), "service-image-reference")
            self.service_images[service] = reference

    def build(self) -> None:
        self.stage = "build-application"
        self.command(
            ("build", "--file", "Dockerfile", "--tag", f"{self.project}:app", "."), timeout=900
        )
        self.stage = "build-browser"
        self.command(
            (
                "build",
                "--file",
                "docker/ci/connected-browser.Dockerfile",
                "--tag",
                f"{self.project}:browser",
                ".",
            ),
            timeout=900,
        )
        self.stage = "admit-trust-bundle"
        for role in ("app", "browser"):
            identity = self.command(
                ("image", "inspect", "--format", "{{.Id}}", f"{self.project}:{role}")
            ).strip()
            require(re.fullmatch(r"sha256:[0-9a-f]{64}", identity) is not None, "image-identity")
            self.image_ids[role] = identity
        bundle = self.command(
            (
                "run",
                "--rm",
                "--name",
                self.project + "-trust-bundle",
                "--label",
                f"com.docker.compose.project={self.project}",
                "--label",
                "com.docker.compose.service=trust-bundle",
                "--network",
                "none",
                "--read-only",
                "--log-driver",
                "none",
                "--no-healthcheck",
                "--ulimit",
                "core=0:0",
                "--entrypoint",
                "python",
                f"{self.project}:app",
                "-c",
                "import pathlib,ssl; p=pathlib.Path(ssl.get_default_verify_paths().cafile or ''); "
                "assert p.resolve()==pathlib.Path('/etc/ssl/certs/ca-certificates.crt'); "
                "print(p.read_text(),end='')",
            )
        )
        require("-----BEGIN CERTIFICATE-----" in bundle, "image-trust-bundle")
        write_private(
            self.fixture.directory / "ca-bundle.pem",
            bundle.encode() + (self.fixture.directory / "ca.pem").read_bytes(),
        )

    def seed_keycloak(self) -> None:
        self.stage = "seed-keycloak-runtime"
        reference = self.service_images["keycloak"]
        self.command(("pull", reference), timeout=180)
        container = self.command(
            (
                "create",
                "--name",
                self.project + "-keycloak-seed",
                "--label",
                f"com.docker.compose.project={self.project}",
                "--label",
                "com.docker.compose.service=keycloak-seed",
                "--network",
                "none",
                "--read-only",
                "--log-driver",
                "none",
                "--no-healthcheck",
                "--ulimit",
                "core=0:0",
                reference,
            )
        ).strip()
        require(re.fullmatch(r"[0-9a-f]{64}", container) is not None, "keycloak-seed-identity")
        folder = self.fixture.directory / "runtime/keycloak-quarkus"
        try:
            inspected = json.loads(self.command(("inspect", container)))
            require(
                len(inspected) == 1
                and inspected[0]["Id"] == container
                and inspected[0]["Config"]["Labels"].get("com.docker.compose.project")
                == self.project
                and inspected[0]["Config"]["Image"] == reference
                and inspected[0]["State"]["Status"] == "created",
                "keycloak-seed-owner",
            )
            self.command(("cp", f"{container}:/opt/keycloak/lib/quarkus/.", str(folder)))
            paths: list[Path] = []
            for path in folder.rglob("*"):
                require(len(paths) < 4096, "keycloak-seed-population")
                paths.append(path)
            require(0 < len(paths) <= 4096, "keycloak-seed-population")
            require(
                all(not path.is_symlink() and (path.is_file() or path.is_dir()) for path in paths),
                "keycloak-seed-kind",
            )
            require(
                sum(path.stat().st_size for path in paths if path.is_file()) <= 512 * 1024 * 1024,
                "keycloak-seed-byte-bound",
            )
            workspace = self.volatile_workspace()
            for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
                workspace.own(path, uid=1000, gid=0, mode=path.stat().st_mode & 0o777)
            workspace.own(folder, uid=1000, gid=0, mode=0o700)
        finally:
            self.remove_owned_container(container)

    def remove_owned_container(self, container: str) -> None:
        inspected = json.loads(self.command(("inspect", container)))
        require(
            len(inspected) == 1
            and inspected[0]["Id"] == container
            and inspected[0]["Config"]["Labels"].get("com.docker.compose.project") == self.project,
            "container-cleanup-owner",
        )
        self.command(("rm", "--force", container))

    def create_service(self, service: str) -> str:
        require(service not in self.containers, "duplicate-service-container")
        self.volatile_workspace().verify()
        self.compose_command("create", "--no-build", service)
        container = self.compose_command("ps", "--all", "--quiet", service).strip()
        require(re.fullmatch(r"[0-9a-f]{64}", container) is not None, "service-container-identity")
        inspected = json.loads(self.command(("inspect", container)))
        require(len(inspected) == 1, "service-container-population")
        image = self.command(
            ("image", "inspect", "--format", "{{.Id}}", self.service_images[service])
        ).strip()
        admit_container_storage(
            inspected[0],
            directory=self.fixture.directory,
            project=self.project,
            expected_id=container,
            expected_image=image,
            expected_user=expected_user(service),
            expected_mounts=expected_mounts(service, self.fixture.directory, ROOT),
        )
        require(inspected[0]["State"]["Status"] == "created", "service-started-before-admission")
        self.containers[service] = container
        return container

    def start_service(self, service: str) -> None:
        container = self.create_service(service)
        self.attached.start(service, container)
        self._stream_services.add(service)

    def wait_postgres(self) -> None:
        deadline = time.monotonic() + self.budget.timeout(90)
        while time.monotonic() < deadline:
            self.attached.assert_running()
            result = spawn(
                "docker",
                local_docker_arguments(
                    ("exec", self.containers["postgres"], "pg_isready", "-U", "postgres")
                ),
                cwd=ROOT,
                env=self.environment,
                max_buffer=8192,
                timeout_seconds=self.budget.timeout(5),
                decode_errors="surrogateescape",
            )
            data = (result.stdout + result.stderr).encode("utf-8", "surrogateescape")
            if data:
                admit_logs(data, self.fixture.canaries)
            require(
                result.error is None and result.failure_kind is None, "database-readiness-provider"
            )
            if result.status == 0:
                return
            require(result.status in {1, 2}, "database-readiness-provider")
            time.sleep(self.budget.timeout(0.2))
        raise WitnessFailure("database-readiness-deadline")

    def start(self) -> str:
        self.stage = "database-start"
        self.start_service("postgres")
        self.wait_postgres()
        network = json.loads(self.command(("network", "inspect", f"{self.project}_isolated")))
        require(
            len(network) == 1
            and network[0]["Internal"] is True
            and network[0]["Labels"]["com.docker.compose.project"] == self.project,
            "runtime-network",
        )
        self.stage = "database-provision"
        self.command(("start", "--attach", self.create_service("database-provision")))
        self.stage = "database-migrate"
        self.command(("start", "--attach", self.create_service("migrate")))
        self.stage = "identity-start"
        self.start_service("keycloak")
        self.start_service("proxy")
        self.stage = "identity-readiness"
        self.wait_urls(
            ["https://auth.example.test/realms/coordinator/.well-known/openid-configuration"]
        )
        self.stage = "application-start"
        self.start_service("backend")
        self.stage = "application-readiness"
        self.wait_urls(["https://coordinator.test/healthz"])
        self.stage = "application-identity"
        container = self.compose_command("ps", "--quiet", "backend").strip()
        require(re.fullmatch(r"[0-9a-f]{64}", container) is not None, "application-container")
        actual_image = self.command(("inspect", "--format", "{{.Image}}", container)).strip()
        require(actual_image == self.image_ids["app"], "application-image-drift")
        self.stage = "database-endpoint"
        database = self.compose_command("ps", "--quiet", "postgres").strip()
        require(re.fullmatch(r"[0-9a-f]{64}", database) is not None, "database-container")
        return database_host(
            self.command(("network", "inspect", f"{self.project}_isolated")),
            project=self.project,
            container=database,
        )

    def wait_urls(self, urls: list[str]) -> None:
        self.volatile_workspace().verify()
        self._readiness_sequence += 1
        readiness_milliseconds = max(1, int(self.budget.timeout(120) * 1000))
        mounts = expected_mounts("readiness", self.fixture.directory, ROOT)
        bindings = tuple(
            argument
            for destination, (source, writable) in mounts.items()
            for argument in (
                "--mount",
                f"type=bind,src={source},dst={destination}" + ("" if writable else ",readonly"),
            )
        )
        container = self.command(
            (
                "create",
                "--name",
                f"{self.project}-readiness-{self._readiness_sequence}",
                "--label",
                f"com.docker.compose.project={self.project}",
                "--label",
                "com.docker.compose.service=readiness",
                "--network",
                f"{self.project}_isolated",
                "--read-only",
                "--log-driver",
                "none",
                "--no-healthcheck",
                "--ulimit",
                "core=0:0",
                *bindings,
                "--env",
                "NODE_EXTRA_CA_CERTS=/fixture/ca.pem",
                "--entrypoint",
                "node",
                self.image_ids["browser"],
                "--input-type=module",
                "-e",
                _READY,
                json.dumps(urls),
                str(readiness_milliseconds),
            )
        ).strip()
        require(
            re.fullmatch(r"[0-9a-f]{64}", container) is not None, "readiness-container-identity"
        )
        try:
            inspected = json.loads(self.command(("inspect", container)))
            require(len(inspected) == 1, "readiness-container-population")
            admit_container_storage(
                inspected[0],
                directory=self.fixture.directory,
                project=self.project,
                expected_id=container,
                expected_image=self.image_ids["browser"],
                expected_user=expected_user("readiness"),
                expected_mounts=mounts,
            )
            require(
                inspected[0]["State"]["Status"] == "created", "readiness-started-before-admission"
            )
            self.command(("start", "--attach", container), timeout=150, readiness=True)
        finally:
            self.remove_owned_container(container)

    def browser(self, host: str) -> None:
        self.stage = "browser-journey"
        container = self.create_service("browser")
        stop = threading.Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                spawn,
                "docker",
                local_docker_arguments(("start", "--attach", container)),
                cwd=ROOT,
                env=self.environment,
                max_buffer=MAX_OUTPUT,
                timeout_seconds=self.budget.timeout(240),
                stop_requested=stop.is_set,
                decode_errors="surrogateescape",
            )
            try:
                with psycopg.connect(
                    host=host,
                    port=5432,
                    dbname="ci_coordinator",
                    user="postgres",
                    password=self.fixture.database_password,
                    connect_timeout=5,
                    autocommit=True,
                    options="-c default_transaction_read_only=on -c statement_timeout=5000",
                ) as connection:
                    for index, stage in enumerate(STAGES):
                        self.stage = stage
                        name = f"{index:02d}-{stage}"
                        path = self.fixture.directory / "evidence" / f"{name}.json"
                        deadline = time.monotonic() + self.budget.timeout(120)
                        while not path.exists():
                            self.attached.assert_running()
                            require(
                                not future.done() and time.monotonic() < deadline,
                                "browser-checkpoint-missing",
                            )
                            stop.wait(self.budget.timeout(0.1))
                        require(
                            path.is_file()
                            and not path.is_symlink()
                            and path.stat().st_size <= 8192,
                            "checkpoint-file",
                        )
                        document = json.loads(path.read_bytes())
                        require(type(document) is dict, "checkpoint-document")
                        verify_checkpoint(
                            connection, document, stage=stage, policy_key=self.fixture.policy_key
                        )
                        self.budget.timeout(1)
                        self.checkpoints.append(stage)
                        write_private(path.with_suffix(".ack"), "accepted")
                    result = future.result(timeout=self.budget.timeout(40))
                    data = (result.stdout + result.stderr).encode("utf-8", "surrogateescape")
                    admit_logs(data, self.fixture.canaries)
                    require(
                        result.status == 0 and result.error is None and result.failure_kind is None,
                        "browser-execution",
                    )
            finally:
                stop.set()
                if sys.exc_info()[0] is not None:
                    self.begin_cleanup()
                result = future.result(timeout=self.budget.timeout(40))
                self.browser_exit = result.status
                data = (result.stdout + result.stderr).encode("utf-8", "surrogateescape")
                progress = self.fixture.directory / "evidence/browser-progress.json"
                self.browser_diagnostic = browser_diagnostic(
                    result.stdout + result.stderr,
                    progress.read_text()
                    if progress.is_file()
                    and not progress.is_symlink()
                    and progress.stat().st_size <= 4096
                    else "",
                )
                if result.status != 0:
                    self.browser_failure = next(
                        (
                            code
                            for code in sorted(_BROWSER_FAILURES)
                            if f"Error: {code}".encode() in data
                        ),
                        "browser-assertion-or-provider",
                    )
                if data:
                    self.scan_log("browser-process", data)
                console = self.fixture.directory / "evidence/browser-console.log"
                if console.is_file():
                    require(
                        not console.is_symlink() and console.stat().st_size <= MAX_OUTPUT,
                        "console-file",
                    )
                    self.scan_log("browser-console", console.read_bytes())

    def scan_log(self, name: str, data: bytes) -> None:
        admit_logs(data, self.fixture.canaries)
        self.log_receipts[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def service_logs(self, *, diagnostic_only: bool = False) -> None:
        if diagnostic_only:
            self._logs_terminal = True
        else:
            self.attached.assert_running()
        for service in sorted(self._stream_services):
            if service not in self.service_states:
                self.service_states[service] = self.service_state(service)
            if not diagnostic_only:
                state = self.service_states[service]
                require(
                    state.get("availability") == "observed"
                    and state.get("status") == "running"
                    and state.get("running") is True
                    and state.get("oomKilled") is False
                    and state.get("providerError") is False,
                    "service-ended-before-stop",
                )
        if not diagnostic_only:
            self.attached.assert_running()
        self._logs_terminal = True
        self.compose_command("stop", "--timeout", "40", "backend", "keycloak", "proxy", "postgres")
        exit_codes: dict[str, int] = {}
        for service in sorted(self._stream_services):
            container = self.containers[service]
            inspected = json.loads(self.command(("inspect", container)))
            require(
                len(inspected) == 1
                and inspected[0]["Id"] == container
                and inspected[0]["Config"]["Labels"].get("com.docker.compose.project")
                == self.project,
                "log-container-owner",
            )
            state = inspected[0]["State"]
            require(
                state["Running"] is False
                and state["Status"] == "exited"
                and state["OOMKilled"] is False
                and not state["Error"]
                and type(state["ExitCode"]) is int
                and 0 <= state["ExitCode"] <= 255,
                "log-container-terminal-state",
            )
            exit_codes[service] = state["ExitCode"]
        captured = self.attached.finish(exit_codes)
        for service, output in captured.items():
            data = output.stdout + output.stderr
            self.scan_log(service, data)
            if service == "backend":
                fields = frozenset(
                    line.partition("=")[0]
                    for line in (self.fixture.directory / "backend.env").read_text().splitlines()
                )
                self.backend_startup = startup_diagnostic(
                    data.decode("utf-8", "replace"),
                    allowed_fields=fields
                    | frozenset(field.removesuffix("_FILE") for field in fields),
                )
        require(set(captured) == {"backend", "keycloak", "proxy", "postgres"}, "log-population")

    def service_state(self, service: str) -> dict[str, object]:
        try:
            container = self.compose_command("ps", "--all", "--quiet", service).strip()
            if not container:
                return {"availability": "absent"}
            if re.fullmatch(r"[0-9a-f]{64}", container) is None:
                return {"availability": "unavailable"}
            return container_diagnostic(
                self.command(("inspect", "--format", "{{json .State}}", container))
            )
        except WitnessFailure as error:
            if str(error) in {"whole-witness-deadline", "secret-disclosure"}:
                raise
            return {"availability": "unavailable"}

    def cleanup(self) -> None:
        self._logs_terminal = True
        try:
            if not self.owned:
                return
            self.compose_command(
                "down", "--volumes", "--remove-orphans", "--timeout", "20", timeout=120
            )
            for resource in ("container", "network", "volume"):
                listing = self.command(
                    (
                        resource,
                        "ls",
                        *(("--all",) if resource == "container" else ()),
                        "--quiet",
                        "--filter",
                        f"label=com.docker.compose.project={self.project}",
                    )
                )
                require(not listing.strip(), "resource-cleanup")
            for tag in ("browser", "app"):
                reference = f"{self.project}:{tag}"
                if self.command(
                    ("image", "ls", "--quiet", "--filter", f"reference={reference}")
                ).strip():
                    self.command(("image", "rm", reference))
        finally:
            self.attached.close()

    def begin_cleanup(self) -> None:
        remaining = self.budget.begin_cleanup()
        signal.setitimer(signal.ITIMER_REAL, remaining)


def main() -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux":
        print(json.dumps({"status": "failed", "reason": "github-linux-runner-required"}))
        return 1
    project = "ci-business-" + secrets.token_hex(12)
    runner: Runner | None = None
    status = "failed"
    reason = "unclassified"
    cleanup = "not-started"
    failure_log_collection = "not-needed"
    primary_failure: str | None = None
    execution_error_class: str | None = None
    budget = WitnessBudget()
    workspace = VolatileWorkspace(project, budget)

    def expire(_signum: int, _frame: FrameType | None) -> None:
        # Interrupted subprocess drain consumes the cleanup reserve as well.
        if budget.cleanup_deadline is None:
            signal.setitimer(signal.ITIMER_REAL, budget.begin_cleanup())
        raise WitnessFailure("whole-witness-deadline")

    def terminate(_signum: int, _frame: FrameType | None) -> None:
        signal.setitimer(signal.ITIMER_REAL, budget.begin_cleanup())
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, terminate)
    previous_alarm_handler = signal.signal(signal.SIGALRM, expire)
    previous_alarm = signal.setitimer(signal.ITIMER_REAL, budget.timeout(EXECUTION_SECONDS))
    try:
        admit_docker_environment(os.environ)
        workspace.prepare()
        fixture = prepare_fixture(workspace, ROOT)
        prepare_runtime_directories(workspace)
        runner = Runner(fixture, project, budget, workspace)
        runner.assert_unallocated()
        try:
            admit_logs(b"scanner-positive-control:" + fixture.canaries[0], fixture.canaries)
        except WitnessFailure as error:
            require(str(error) == "secret-disclosure", "scanner-control")
        else:
            raise WitnessFailure("scanner-control")
        runner.configure()
        runner.build()
        runner.seed_keycloak()
        apply_fixture_ownership(workspace)
        runner.stage = "log-transport-qualification"
        runner.log_transport = qualify_attached_logs(
            root=ROOT,
            environment=runner.environment,
            budget=budget,
            image=runner.image_ids["app"],
            project=project,
        )
        host = runner.start()
        runner.browser(host)
        runner.attached.assert_running()
        runner.service_logs()
        workspace.verify()
        workspace.receipt["terminalAdmitted"] = True
        require(
            set(runner.log_receipts)
            == {"backend", "keycloak", "proxy", "postgres", "browser-console", "browser-process"},
            "log-inventory",
        )
        budget.timeout(1)
        status, reason = "passed", "complete"
    except WitnessFailure as error:
        reason = str(error)
    except (Exception, KeyboardInterrupt) as error:
        reason = "execution-error"
        kind = type(error).__name__
        execution_error_class = (
            kind
            if kind
            in {
                "PermissionError",
                "FileNotFoundError",
                "TimeoutError",
                "KeyboardInterrupt",
                "JSONDecodeError",
                "ValueError",
                "TypeError",
                "KeyError",
                "OSError",
            }
            else "unclassified"
        )
    finally:
        primary_failure = reason if status == "failed" else None
        if (
            runner is not None
            and status == "failed"
            and runner.owned
            and budget.cleanup_deadline is None
            and budget.remaining() > 0
        ):
            try:
                runner.service_logs(diagnostic_only=True)
                failure_log_collection = "complete"
            except (Exception, KeyboardInterrupt):
                failure_log_collection = "unavailable"
        try:
            signal.setitimer(signal.ITIMER_REAL, budget.begin_cleanup())
            if runner is not None:
                runner.cleanup()
            workspace.close()
            budget.timeout(1)
            cleanup = "complete"
        except (Exception, KeyboardInterrupt):
            status, reason, cleanup = "failed", "cleanup-unproved", "failed"
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, *previous_alarm)
        signal.signal(signal.SIGTERM, previous_handler)
    receipt = {
        "schemaVersion": "connected-administrator-witness/v1",
        "project": project,
        "status": status,
        "reason": reason,
        "stage": runner.stage if runner else "fixture",
        "cleanup": cleanup,
        "checkpoints": runner.checkpoints if runner else [],
        "logs": runner.log_receipts if runner else {},
        "imageIds": runner.image_ids if runner else {},
        "browserExit": runner.browser_exit if runner else None,
        "browserFailure": runner.browser_failure if runner else None,
        "browserDiagnostic": runner.browser_diagnostic if runner else {},
        "failureLogCollection": failure_log_collection,
        "primaryFailure": primary_failure,
        "executionErrorClass": execution_error_class,
        "executionBudgetSeconds": EXECUTION_SECONDS,
        "cleanupBudgetSeconds": CLEANUP_SECONDS,
        "dockerHost": LOCAL_DOCKER_HOST,
        "storage": workspace.receipt,
        "logTransport": runner.log_transport if runner else {},
        "dockerVersions": runner.docker_versions if runner else {},
        "providerFailures": runner.provider_failures if runner else [],
        "readinessChecks": runner.readiness_checks if runner else [],
        "serviceStates": runner.service_states if runner else {},
        "backendStartup": runner.backend_startup if runner else {},
        "sourceCommit": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }
    output = ROOT / ".ci-evidence/connected-administrator.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if status == "passed" else 1
