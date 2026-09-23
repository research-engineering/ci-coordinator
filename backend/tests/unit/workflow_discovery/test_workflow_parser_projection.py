from __future__ import annotations

from textwrap import indent

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.workflow_discovery.parser import parse_workflow
from ci_coordinator.workflow_discovery.source import WorkflowSource, git_blob_sha1
from ci_coordinator.workflow_discovery.summary import ParsedWorkflow

SCOPE = RepositoryScope(7, 11)
REVISION = "a" * 40


def _document(
    *,
    trigger: str = "push",
    root_extra: str = "",
    job_body: str = "name: Full CI\nruns-on: ubuntu-latest\nsteps: []\n",
) -> str:
    return f"name: CI\non: {trigger}\n{root_extra}jobs:\n  test:\n{indent(job_body, '    ')}"


def test_rich_static_workflow_projects_declared_syntax_without_runtime_claims() -> None:
    parsed = _parse(
        """\
name: Rich CI
on: [push, pull_request, push]
permissions: read-all
concurrency:
  group: repository-ci
  cancel-in-progress: true
jobs:
  test:
    name: Matrix tests
    needs: [lint, build, lint]
    runs-on: [self-hosted, linux]
    permissions:
      contents: read
      id-token: write
    environment:
      name: staging
    services:
      postgres:
        image: postgres:18.6
    timeout-minutes: 20
    concurrency: matrix-tests
    strategy:
      matrix:
        python: ["3.13", "3.14"]
        debug: [true, false]
    if: success()
    env:
      TOKEN: ${{ secrets.API_TOKEN }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        if: always()
      - run: echo omitted-script-body
"""
    )

    summary = parsed.summary
    job = summary.jobs[0]
    assert summary.triggers == ("pull_request", "push")
    assert summary.permissions is not None
    assert (summary.permissions.kind, summary.permissions.all_level) == ("all", "read-all")
    assert summary.concurrency is not None
    assert (summary.concurrency.group, summary.concurrency.cancel_in_progress) == (
        "repository-ci",
        True,
    )
    assert summary.static_secret_names == ("API_TOKEN",)
    assert job.needs == ("build", "lint")
    assert job.runs_on == ("linux", "self-hosted")
    assert job.environment == "staging"
    assert job.service_ids == ("postgres",)
    assert job.timeout_minutes == 20
    assert job.matrix is not None
    assert tuple(item.name for item in job.matrix) == ("debug", "python")
    assert job.provider_signal_name is None
    assert job.steps is not None
    assert job.steps[0].uses == "actions/checkout@v4"
    assert "omitted-script-body" not in repr(parsed)
    assert _reason(parsed, "job.provider_signal_name") == (
        "explicit_non_matrix_native_job_name_not_proven"
    )


@pytest.mark.parametrize(
    ("document", "field", "reason"),
    [
        (_document(trigger="[push, 7]"), "workflow.triggers", "unsupported_trigger_syntax"),
        (_document(trigger="[]"), "workflow.triggers", "dynamic_or_empty_trigger"),
        (
            _document(trigger='"${{ inputs.event }}"'),
            "workflow.triggers",
            "dynamic_or_empty_trigger",
        ),
        (
            _document(root_extra="permissions: admin\n"),
            "workflow.permissions.declared",
            "unsupported_permissions_syntax",
        ),
        (
            _document(root_extra='permissions:\n  contents: "${{ inputs.level }}"\n'),
            "workflow.permissions.declared",
            "dynamic_or_unsupported_permission",
        ),
        (
            _document(root_extra='concurrency: "${{ github.ref }}"\n'),
            "workflow.concurrency",
            "dynamic_concurrency",
        ),
        (
            _document(root_extra='concurrency:\n  group: ci\n  cancel-in-progress: "yes"\n'),
            "workflow.concurrency",
            "dynamic_or_unsupported_concurrency",
        ),
        (
            _document(job_body='name: "${{ matrix.name }}"\nruns-on: ubuntu-latest\n'),
            "job.name",
            "dynamic_or_unsupported_job_name",
        ),
        (
            _document(job_body="name: Full CI\nneeds: {build: true}\nruns-on: ubuntu-latest\n"),
            "job.needs",
            "unsupported_sequence_syntax",
        ),
        (
            _document(job_body='name: Full CI\nruns-on: "${{ inputs.runner }}"\n'),
            "job.runs_on.declared",
            "dynamic_sequence_value",
        ),
        (
            _document(job_body='name: Full CI\nuses: "${{ inputs.workflow }}"\n'),
            "job.uses",
            "dynamic_or_unsupported_job_call",
        ),
        (
            _document(job_body="name: Full CI\nuses: {workflow: invalid}\n"),
            "job.uses",
            "dynamic_or_unsupported_job_call",
        ),
        (
            _document(job_body="name: Full CI\nruns-on: ubuntu-latest\npermissions: execute\n"),
            "job.permissions.declared",
            "unsupported_permissions_syntax",
        ),
        (
            _document(
                job_body=(
                    "name: Full CI\nruns-on: ubuntu-latest\n"
                    'environment: "${{ inputs.environment }}"\n'
                )
            ),
            "job.environment.declared",
            "dynamic_or_unsupported_environment",
        ),
        (
            _document(job_body="name: Full CI\nruns-on: ubuntu-latest\nservices: [postgres]\n"),
            "job.service_ids",
            "unsupported_services_syntax",
        ),
        (
            _document(job_body="name: Full CI\nruns-on: ubuntu-latest\ntimeout-minutes: 0\n"),
            "job.timeout_minutes",
            "dynamic_or_invalid_timeout",
        ),
        (
            _document(job_body="name: Full CI\nruns-on: ubuntu-latest\nstrategy: dynamic\n"),
            "job.matrix",
            "unsupported_strategy_syntax",
        ),
        (
            _document(
                job_body=(
                    "name: Full CI\nruns-on: ubuntu-latest\n"
                    "strategy:\n  matrix:\n    include: [{python: '3.14'}]\n"
                )
            ),
            "job.matrix",
            "matrix_include_exclude_or_expression_not_projected",
        ),
        (
            _document(job_body="name: Full CI\nruns-on: ubuntu-latest\nif: {always: true}\n"),
            "job.condition.syntax",
            "unsupported_condition_syntax",
        ),
        (
            _document(job_body="name: Full CI\nruns-on: ubuntu-latest\nsteps: {run: pytest}\n"),
            "job.step_run_presence",
            "unsupported_steps_syntax",
        ),
        (
            _document(job_body="name: Full CI\nruns-on: ubuntu-latest\nsteps: [pytest]\n"),
            "job.step_uses",
            "unsupported_step_syntax",
        ),
        (
            _document(
                job_body=("name: Full CI\nruns-on: ubuntu-latest\nsteps:\n  - name: {value: bad}\n")
            ),
            "job.step_run_presence",
            "unsupported_step_name",
        ),
        (
            _document(
                job_body=("name: Full CI\nruns-on: ubuntu-latest\nsteps:\n  - uses: {value: bad}\n")
            ),
            "job.step_run_presence",
            "unsupported_step_uses",
        ),
        (
            _document(job_body="name: Full CI\nruns-on: ubuntu-latest\nsteps:\n  - if: [always]\n"),
            "job.step_run_presence",
            "unsupported_step_condition",
        ),
        (
            _document(
                job_body=(
                    "name: Full CI\nruns-on: ubuntu-latest\nsteps:\n"
                    '  - uses: "${{ inputs.action }}"\n'
                )
            ),
            "job.step_uses",
            "dynamic_step_uses",
        ),
        (
            _document(
                job_body=(
                    "name: Full CI\n"
                    "uses: example/repo/.github/workflows/ci.yml@main\n"
                    "secrets: inherit\n"
                )
            ),
            "job.secrets.static_names",
            "dynamic_or_inherited_secret_names",
        ),
        (
            _document(
                job_body=(
                    "name: Full CI\nruns-on: ubuntu-latest\nenv:\n"
                    '  TOKEN: "${{ secrets[inputs.name] }}"\n'
                )
            ),
            "job.secrets.static_names",
            "dynamic_or_inherited_secret_names",
        ),
    ],
)
def test_unsupported_or_dynamic_syntax_closes_as_typed_unknown(
    document: str,
    field: str,
    reason: str,
) -> None:
    parsed = _parse(document)

    assert _reason(parsed, field) == reason


def _parse(document: str) -> ParsedWorkflow:
    content = document.encode()
    source = WorkflowSource(
        ".github/workflows/ci.yml",
        git_blob_sha1(content),
        len(content),
        content,
    )
    parsed = parse_workflow(
        source,
        scope=SCOPE,
        revision=REVISION,
        default_branch="master",
    )
    assert isinstance(parsed, ParsedWorkflow)
    return parsed


def _reason(parsed: ParsedWorkflow, field: str) -> str:
    matches = [item.reason for item in parsed.unknowns if item.field == field]
    assert len(matches) == 1
    return matches[0]
