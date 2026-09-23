from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import time
import urllib.request
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import cast
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_BYTES = 131_072
MAX_INTEGER = 9_007_199_254_740_991
UPLOAD_SECONDS = 15
API_VERSION = "2026-03-10"
_DIGEST = re.compile(r"[0-9a-f]{64}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _json(content: bytes, *, maximum: int = MAX_BYTES) -> dict[str, object]:
    if len(content) > maximum:
        raise ValueError("JSON exceeds limit")
    value = json.loads(content, object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return cast(dict[str, object], value)


def _positive(value: object) -> int:
    if type(value) is str and re.fullmatch(r"[1-9][0-9]{0,15}", value):
        value = int(value)
    if type(value) is not int or not 1 <= value <= MAX_INTEGER:
        raise ValueError("positive safe ID required")
    return value


def _text(value: object, maximum: int = 8_192) -> str:
    if (
        type(value) is not str
        or not 0 < len(value) <= maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("bounded text required")
    return value


def _https(value: object) -> str:
    url = _text(value, 4_096)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("HTTPS without credentials or fragments required")
    return url


def _request(
    url: str, token: str, *, payload: dict[str, object] | None = None
) -> dict[str, object]:
    _https(url)
    headers = {
        "Authorization": "Bearer " + _text(token),
        "Accept": "application/json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "ci-measurement-reporter/v1",
    }
    body = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    if body is not None:
        if len(body) > MAX_BYTES:
            raise ValueError("report exceeds limit")
        headers["Content-Type"] = "application/json"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    # HTTPS is admitted above; redirects cannot forward credentials elsewhere.
    request = urllib.request.Request(url, data=body, headers=headers)  # noqa: S310
    try:
        response = opener.open(request, timeout=5)
    except HTTPError as error:
        error.close()
        raise
    with response:
        if response.status not in (200, 201):
            raise ValueError("request rejected")
        return _json(response.read(1_048_577), maximum=1_048_576)


def _script_digest() -> str:
    descriptor = os.open(
        __file__, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise ValueError("reporter source must be a bounded regular file")
        content = source.read(MAX_BYTES + 1)
        after = os.fstat(source.fileno())
    if len(content) > MAX_BYTES or (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("reporter source changed")
    return hashlib.sha256(content).hexdigest()


def _cpu() -> tuple[float, float] | str:
    try:
        import resource
    except ImportError:
        return "unsupported_platform"
    try:
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        return usage.ru_utime, usage.ru_stime
    except (OSError, ValueError):
        return "counter_error"


def _counter(name: str, value: int | None, reason: str | None = None) -> dict[str, object]:
    if value is not None and not 0 <= value <= MAX_INTEGER:
        value, reason = None, "out_of_range"
    return {
        "counter": name,
        "unit": "microsecond",
        "scope": "reporter_interval" if name == "elapsed" else "waited_children",
        "value": value,
        "unavailableReason": reason,
    }


def _measure(command: Sequence[str]) -> tuple[int, list[dict[str, object]]]:
    child: subprocess.Popen[bytes] | None = None
    interrupted: int | None = None

    def forward(number: int, _frame: FrameType | None) -> None:
        nonlocal interrupted
        interrupted = number
        if child is not None:
            with suppress(ProcessLookupError):
                child.send_signal(number)

    previous = {
        number: signal.signal(number, forward) for number in (signal.SIGINT, signal.SIGTERM)
    }
    before = _cpu()
    started = time.monotonic_ns()
    try:
        try:
            child = subprocess.Popen(command)  # noqa: S603 -- Explicit caller argv; no shell expansion.
        except FileNotFoundError:
            code = 127
        except OSError:
            code = 126
        else:
            if interrupted is not None:
                forward(interrupted, None)
            code = child.wait()
    finally:
        elapsed = (time.monotonic_ns() - started) // 1_000
        after = _cpu()
        for number, handler in previous.items():
            signal.signal(number, handler)
    counters = [_counter("elapsed", elapsed)]
    for index, name in enumerate(("cpu_user", "cpu_system")):
        if isinstance(before, str) or isinstance(after, str):
            counters.append(
                _counter(name, None, before if isinstance(before, str) else cast(str, after))
            )
            continue
        difference = (after[index] - before[index]) * 1_000_000
        if not math.isfinite(difference):
            counters.append(_counter(name, None, "counter_error"))
        elif difference < 0:
            counters.append(_counter(name, None, "out_of_range"))
        else:
            counters.append(_counter(name, round(difference)))
    return code, counters


def _job_context(env: dict[str, str], check_run_id: int) -> tuple[dict[str, object], int, int]:
    repository = _text(env["GITHUB_REPOSITORY"], 140)
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise ValueError("repository name invalid")
    run_id, attempt = _positive(env["GITHUB_RUN_ID"]), _positive(env["GITHUB_RUN_ATTEMPT"])
    base = f"https://api.github.com/repos/{repository}"
    token = env["CI_REPORT_GITHUB_TOKEN"]
    path = f"{base}/actions/runs/{run_id}/attempts/{attempt}"
    run = _request(path, token)
    repo = run.get("repository")
    if (
        type(repo) is not dict
        or _positive(repo.get("id")) != _positive(env["GITHUB_REPOSITORY_ID"])
        or _positive(run.get("id")) != run_id
        or _positive(run.get("run_attempt")) != attempt
        or re.fullmatch(r"[0-9a-f]{40}", _text(run.get("head_sha"), 40)) is None
    ):
        raise ValueError("attempt response mismatch")
    for page in range(1, 21):
        result = _request(f"{path}/jobs?per_page=100&page={page}", token)
        jobs = result.get("jobs")
        if type(jobs) is not list or len(jobs) > 100:
            raise ValueError("job page invalid")
        matches = [
            job
            for job in jobs
            if type(job) is dict and job.get("check_run_url") == f"{base}/check-runs/{check_run_id}"
        ]
        if len(matches) > 1:
            raise ValueError("job binding ambiguous")
        if matches:
            job = matches[0]
            if _positive(job.get("run_id")) != run_id or job.get("head_sha") != run["head_sha"]:
                raise ValueError("job response mismatch")
            return run, _positive(job.get("id")), page
        if len(jobs) < 100:
            break
    raise ValueError("job not found within bound")


def _digest(value: dict[str, object]) -> str:
    # The admitted report uses ASCII keys/values and safe integers only.
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _upload(value: dict[str, object], env: dict[str, str]) -> dict[str, object]:
    endpoint = _https(value["endpoint"])
    parsed = urlsplit(endpoint)
    if parsed.path not in ("", "/") or parsed.query:
        raise ValueError("coordinator endpoint must be an origin")
    audience_base = _text(value["audience"], 512)
    audience = (
        "urn:ci-coordinator:measurement-report:v1:"
        + hashlib.sha256(audience_base.encode()).hexdigest()
    )
    oidc_url = urlsplit(_https(env["ACTIONS_ID_TOKEN_REQUEST_URL"]))
    if (
        not oidc_url.hostname
        or not oidc_url.hostname.endswith(".actions.githubusercontent.com")
        or oidc_url.port not in (None, 443)
    ):
        raise ValueError("OIDC request host invalid")
    query = parse_qsl(oidc_url.query, strict_parsing=True)
    if any(name == "audience" for name, _ in query):
        raise ValueError("OIDC audience already supplied")
    token_url = urlunsplit(oidc_url._replace(query=urlencode([*query, ("audience", audience)])))
    token = _text(_request(token_url, env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]).get("value"))
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("token lookup hint invalid")
    claims = _json(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    check_run_id = _positive(claims.get("check_run_id"))
    run, job_id, page = _job_context(env, check_run_id)
    report = {
        "schemaVersion": "ci-economics-job-report/v1",
        "method": "waited_children/v1",
        "attempt": {
            "installationId": _positive(value["installationId"]),
            "repositoryId": _positive(env["GITHUB_REPOSITORY_ID"]),
            "workflowRunId": _positive(env["GITHUB_RUN_ID"]),
            "runAttempt": _positive(env["GITHUB_RUN_ATTEMPT"]),
            "headSha": run["head_sha"],
        },
        "providerJobId": job_id,
        "checkRunId": check_run_id,
        "sampleKey": value["sampleKey"],
        "producerDigest": value["producerDigest"],
        "workload": value["workload"],
        "reportedAt": value["reportedAt"],
        "commandExitCode": value["commandExitCode"],
        "measurements": sorted(
            cast(list[dict[str, object]], value["measurements"]),
            key=lambda counter: _text(counter["counter"]),
        ),
    }
    response = _request(
        endpoint.rstrip("/") + "/api/v2/economics/reports",
        token,
        payload={
            "repository": env["GITHUB_REPOSITORY"],
            "ref": env["GITHUB_REF"],
            "eventName": env["GITHUB_EVENT_NAME"],
            "executionSha": env["GITHUB_SHA"],
            "jobPage": page,
            "report": report,
        },
    )
    receipt = {
        "schemaVersion": "ci-economics-report-receipt/v1",
        "ok": True,
        "status": response.get("status"),
        "reportId": _digest(
            {
                "schemaVersion": "ci-economics-job-report-identity/v1",
                "attempt": report["attempt"],
                "providerJobId": job_id,
                "sampleKey": value["sampleKey"],
            }
        ),
        "reportDigest": _digest(report),
    }
    if (
        response.get("ok") is not True
        or response.get("status") not in ("recorded", "replayed")
        or response != receipt
    ):
        raise ValueError("report receipt does not match the request")
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--_submit"]:
        try:
            receipt = _upload(_json(sys.stdin.buffer.read(MAX_BYTES + 1)), dict(os.environ))
            print(json.dumps(receipt, separators=(",", ":")), flush=True)
        except Exception:
            return 1
        return 0
    parser = argparse.ArgumentParser(prog="ci-measurement-reporter")
    for name in (
        "endpoint",
        "audience",
        "installation-id",
        "sample-key",
        "inputs-digest",
        "runner-digest",
        "cache-digest",
    ):
        parser.add_argument("--" + name)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(arguments)
    command = options.command[1:] if options.command[:1] == ["--"] else options.command
    if not command:
        parser.error("a command after -- is required")
    try:
        producer_digest = _script_digest()
    except (OSError, ValueError):
        producer_digest = None
    code, counters = _measure(command)
    try:
        if producer_digest is None or _script_digest() != producer_digest:
            raise ValueError("producer identity unavailable")
        for digest in (options.inputs_digest, options.runner_digest, options.cache_digest):
            if type(digest) is not str or _DIGEST.fullmatch(digest) is None:
                raise ValueError("workload digest required")
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", _text(options.sample_key, 128))
            is None
        ):
            raise ValueError("sample key invalid")
        value = {
            "endpoint": _https(options.endpoint),
            "audience": _text(options.audience, 512),
            "installationId": _positive(options.installation_id),
            "sampleKey": options.sample_key,
            "producerDigest": producer_digest,
            "workload": {
                "protectedInputsDigest": options.inputs_digest,
                "runnerClassDigest": options.runner_digest,
                "cacheClassDigest": options.cache_digest,
            },
            "reportedAt": datetime.now(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "commandExitCode": code,
            "measurements": counters,
        }
        subprocess.run(  # noqa: S603 -- Fixed isolated self-worker; payload travels through stdin.
            [sys.executable, "-I", "-S", str(Path(__file__).resolve()), "--_submit"],
            input=json.dumps(value, allow_nan=False).encode(),
            timeout=UPLOAD_SECONDS,
            stdout=None,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return code if code >= 0 else 128 - code
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())
