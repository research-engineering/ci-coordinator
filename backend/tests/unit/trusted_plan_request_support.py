from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ruamel.yaml import YAML

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FULL_CHECK_PATH = _REPOSITORY_ROOT / ".github/workflows/python-persistence.yml"
WORKFLOW_PATH = _REPOSITORY_ROOT / ".github/workflows/trusted-plan-request.yml"


def load_trusted_request_workflow() -> dict[str, object]:
    return _load_workflow(WORKFLOW_PATH)


def load_full_check_workflow() -> dict[str, object]:
    return _load_workflow(FULL_CHECK_PATH)


def _load_workflow(path: Path) -> dict[str, object]:
    value = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise AssertionError(f"workflow is not a mapping: {path}")
    return value


def run_trusted_request_script(
    tmp_path: Path,
    environment: dict[str, str],
    *,
    oidc_token: str | None = None,
    oidc_error: bool = False,
    fetch_scenario: dict[str, object] | None = None,
) -> dict[str, object]:
    workflow = load_trusted_request_workflow()
    jobs = workflow["jobs"]
    assert type(jobs) is dict
    request = jobs["request"]
    assert type(request) is dict
    steps = request["steps"]
    assert type(steps) is list
    step = steps[0]
    assert type(step) is dict
    inputs = step["with"]
    assert type(inputs) is dict
    script = inputs["script"]
    assert type(script) is str

    script_path = tmp_path / "trusted-request.js"
    harness_path = tmp_path / "trusted-request-harness.cjs"
    scenario_path = tmp_path / "fetch-scenario.json"
    script_path.write_text(script, encoding="utf-8")
    harness_path.write_text(_HARNESS, encoding="utf-8")
    scenario_path.write_text(
        json.dumps(
            fetch_scenario
            if fetch_scenario is not None
            else {"status": 200, "body": '{"schemaVersion":"dynamic-ci-plan/v1"}\n'}
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(harness_path), str(script_path)],
        cwd=_REPOSITORY_ROOT,
        env={
            **os.environ,
            **environment,
            "CI_TEST_FETCH_SCENARIO_PATH": str(scenario_path),
            "CI_TEST_OIDC_ERROR": "true" if oidc_error else "false",
            "CI_TEST_OIDC_TOKEN": "signed-oidc-token" if oidc_token is None else oidc_token,
        },
        check=False,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert type(value) is dict
    return value


def valid_request_environment() -> dict[str, str]:
    return {
        "CI_BASE_SHA": "a" * 40,
        "CI_EVENT": "pull_request",
        "CI_EXECUTION_SHA": "c" * 40,
        "CI_HEAD_SHA": "b" * 40,
        "CI_INSTALLATION_ID": "100",
        "CI_MERGE_GROUP_HEAD_REF": "",
        "CI_OWNER": "example",
        "CI_PLAN_URL": "https://coordinator.example/api/v1/dynamic-ci/plan",
        "CI_PULL_REQUEST_NUMBER": "42",
        "CI_REF": "refs/pull/42/merge",
        "CI_REPO": "target",
        "CI_REPOSITORY_ID": "200",
        "CI_RUN_ATTEMPT": "1",
        "CI_RUN_ID": "300",
    }


_HARNESS = r""""use strict";

const fs = require("node:fs");
const script = fs.readFileSync(process.argv[2], "utf8");
const scenario = JSON.parse(
  fs.readFileSync(process.env.CI_TEST_FETCH_SCENARIO_PATH, "utf8"),
);
const evidence = {
  fetchCalls: [],
  oidcAudiences: [],
  outputs: {},
  secrets: [],
};
const core = {
  getIDToken: async (audience) => {
    evidence.oidcAudiences.push(audience);
    if (process.env.CI_TEST_OIDC_ERROR === "true") {
      throw new Error("injected OIDC failure");
    }
    return process.env.CI_TEST_OIDC_TOKEN;
  },
  setOutput: (name, value) => {
    evidence.outputs[name] = String(value);
  },
  setSecret: (value) => {
    evidence.secrets.push(String(value));
  },
};

global.fetch = async (url, options = {}) => {
  evidence.fetchCalls.push({
    authorization: options.headers?.authorization ?? null,
    body: options.body ?? null,
    contentType: options.headers?.["content-type"] ?? null,
    method: options.method ?? "GET",
    redirect: options.redirect ?? null,
    url: String(url),
  });
  if (scenario.kind === "throw") {
    throw new Error("injected transport failure");
  }
  const body =
    scenario.bodyBytes === undefined
      ? scenario.body ?? ""
      : "x".repeat(scenario.bodyBytes);
  return new Response(body, {
    headers: scenario.headers ?? {},
    status: scenario.status ?? 200,
  });
};

const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
const execute = new AsyncFunction("core", "require", script);
execute(core, require).then(
  () => process.stdout.write(JSON.stringify(evidence)),
  (error) => {
    process.stderr.write(String(error?.stack || error));
    process.exitCode = 1;
  },
);
"""
