from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from package_b_support import make_catalog, make_target_registry

from ci_coordinator.repo_context import (
    ProviderWorkflowInventory,
    RevisionWorkflowCapability,
    StaticRunnerSelector,
    parse_workflow_capability,
)
from ci_coordinator.runner_capacity import (
    CapacityClassSelector,
    ObservedSelfHostedRunner,
    VisibleRunnerGroup,
    project_runner_snapshot,
)
from ci_coordinator.runner_capacity.selector_binding import bind_capacity_class_selectors

_PATH = ".github/workflows/check.yml"
_REVISION = "a" * 40
_LABELS = ("linux", "self-hosted", "x64")
_GROUP_SELECTOR = "{group: Build, labels: [self-hosted, Linux, X64]}"


def _capability(runs_on: str, condition: str = "always()") -> RevisionWorkflowCapability:
    content = (
        "on: push\njobs:\n  test:\n    name: Test\n"
        f"    if: {json.dumps(condition)}\n    runs-on: {runs_on}\n"
        "    env: {LIMIT: 1_000, BITS: 0b101}\n"
    ).encode()
    capability = parse_workflow_capability(content, path=_PATH, revision_sha=_REVISION)
    assert capability is not None
    return capability


@pytest.mark.parametrize("condition", ["always()", "${{ always() }}", "always( )"])
@pytest.mark.parametrize(
    ("runs_on", "raw_runs_on", "labels", "group"),
    [
        ("Linux", "Linux", ("linux",), None),
        ("[self-hosted, Linux, X64]", ["self-hosted", "Linux", "X64"], _LABELS, None),
        ("{group: Build}", {"group": "Build"}, (), "Build"),
        (
            "{group: Build, labels: Linux}",
            {"group": "Build", "labels": "Linux"},
            ("linux",),
            "Build",
        ),
    ],
    ids=["scalar", "sequence", "group", "group-and-scalar"],
)
def test_existing_lexical_projection_and_control_hash_are_unchanged(
    runs_on: str,
    raw_runs_on: object,
    labels: tuple[str, ...],
    group: str | None,
    condition: str,
) -> None:
    capability = _capability(runs_on, condition)
    expected_projection = {
        "job": {"if": condition, "runs-on": raw_runs_on, "env": {"LIMIT": 1000, "BITS": 5}}
    }
    expected_hash = sha256(
        json.dumps(expected_projection, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert capability.to_stable_mapping() == {
        "path": _PATH,
        "jobIds": ["test"],
        "jobNeeds": [["test", []]],
        "providerJobNames": [["test", "Test"]],
        "alwaysJobIds": ["test"] if condition in {"always()", "${{ always() }}"} else [],
        "triggers": ["push"],
        "localReusableWorkflowPaths": [],
        "jobAuthorities": [
            {
                "jobId": "test",
                "controlProjectionHash": expected_hash,
                "conditionContexts": [],
                "declaresContinueOnError": False,
            }
        ],
        "declaresWorkflowEnvironment": False,
        "declaresWorkflowDefaults": False,
        "jobRunnerSelectors": [
            {"jobId": "test", "selector": {"labels": list(labels), "group": group}}
        ],
    }


@pytest.mark.parametrize(
    ("runs_on", "labels", "group"),
    [
        ("{labels: [linux, x64]}", ("linux", "x64"), None),
        (_GROUP_SELECTOR, _LABELS, "Build"),
        ("{group: Build, labels: [LINUX]}", ("linux",), "Build"),
        (
            "\n      group: Build\n      labels:\n        - X64\n        - Linux",
            ("linux", "x64"),
            "Build",
        ),
        ("{labels: [" + "x" * 256 + "]}", ("x" * 256,), None),
        (
            "{labels: [" + ",".join(f"label-{index:02d}" for index in range(16)) + "]}",
            tuple(f"label-{index:02d}" for index in range(16)),
            None,
        ),
    ],
    ids=["labels", "conjunction", "singleton", "block", "text-limit", "label-limit"],
)
def test_mapping_arrays_project_complete_static_selectors(
    runs_on: str,
    labels: tuple[str, ...],
    group: str | None,
) -> None:
    capability = _capability(runs_on)
    assert capability.runner_selector("test") == StaticRunnerSelector(labels, group)
    assert capability.runner_selector("missing") is None


@pytest.mark.parametrize(
    "runs_on",
    [
        "{labels: []}",
        "{group: Build, labels: []}",
        "{group: Build, labels: null}",
        "{group: Build, labels: [linux, '${{ matrix.os }}']}",
        "{group: '${{ inputs.group }}', labels: [linux]}",
        "{group: null, labels: [linux]}",
        "{group: '', labels: [linux]}",
        "{group: [Build], labels: [linux]}",
        "{group: Build, labels: [linux, LINUX]}",
        "{group: Build, labels: [linux, 1]}",
        "{group: Build, labels: [linux, true]}",
        "{group: Build, labels: [linux, null]}",
        "{group: Build, labels: [linux, '']}",
        "{group: Build, labels: [linux, d\u00e9v]}",
        "{group: Build, labels: [linux, [x64]]}",
        "{group: Build, labels: {linux: x64}}",
        "{group: Build, labels: [linux], extra: ignored}",
        "{group: Build, labels: [" + "x" * 257 + "]}",
        "{group: " + "x" * 257 + ", labels: [linux]}",
        "{group: Build, labels: [" + ",".join(f"label-{i}" for i in range(17)) + "]}",
    ],
    ids=[
        "empty",
        "empty-with-group",
        "null-labels",
        "dynamic-label",
        "dynamic-group",
        "null-group",
        "empty-group",
        "sequence-group",
        "duplicate",
        "integer",
        "boolean",
        "null-element",
        "empty-element",
        "non-ascii",
        "nested-sequence",
        "mapping-labels",
        "extra-key",
        "long-label",
        "long-group",
        "too-many-labels",
    ],
)
def test_invalid_mapping_selector_is_unknown_not_partial_or_absent_workflow(runs_on: str) -> None:
    capability = _capability(runs_on)
    assert capability.job_ids == ("test",)
    assert capability.job_runner_selectors == ()
    assert capability.runner_selector("test") is None


def test_selector_canonicalization_does_not_normalize_control_job_hashes() -> None:
    original = _capability(_GROUP_SELECTOR)
    reordered = _capability("{labels: [x64, LINUX, self-hosted], group: Build}")
    assert original.runner_selector("test") == reordered.runner_selector("test")
    assert original.job_authorities[0].control_projection_hash != (
        reordered.job_authorities[0].control_projection_hash
    )


@pytest.mark.parametrize(
    ("second_runs_on", "second_selector", "admitted"),
    [
        (
            "{labels: [X64, Linux, self-hosted], group: Build}",
            StaticRunnerSelector(_LABELS, "Build"),
            True,
        ),
        (
            "{labels: [self-hosted, linux, x64], group: build}",
            StaticRunnerSelector(_LABELS, "build"),
            False,
        ),
        ("{labels: [linux], group: Build}", StaticRunnerSelector(("linux",), "Build"), False),
        ("{labels: [linux, '${{ matrix.os }}'], group: Build}", None, False),
        ("null", None, False),
    ],
    ids=["equivalent", "group-case", "different-labels", "dynamic", "missing"],
)
def test_mapping_array_class_binding_requires_every_profile_and_preserves_conjunction(
    second_runs_on: str,
    second_selector: StaticRunnerSelector | None,
    admitted: bool,
) -> None:
    catalog = make_catalog()
    catalog = replace(
        catalog,
        witnesses=(
            replace(catalog.witnesses[0], execution_profile_id="python-extra"),
            *catalog.witnesses[1:],
        ),
        execution_profiles=(
            catalog.execution_profiles[0],
            replace(catalog.execution_profiles[0], profile_id="python-extra"),
        ),
    )
    registry = make_target_registry(catalog)
    content = "on: push\njobs:\n" + "".join(
        f"  {profile.job_id}:\n    runs-on: {runs_on}\n"
        for profile, runs_on in zip(
            registry.profiles, (_GROUP_SELECTOR, second_runs_on), strict=True
        )
    )
    capability = parse_workflow_capability(
        content.encode(), path=registry.workflows[0].workflow_path, revision_sha=_REVISION
    )
    assert capability is not None
    assert capability.runner_selector(registry.profiles[0].job_id) == StaticRunnerSelector(
        _LABELS, "Build"
    )
    assert capability.runner_selector(registry.profiles[1].job_id) == second_selector
    inventory = ProviderWorkflowInventory(_REVISION, (), (capability,))
    selectors = bind_capacity_class_selectors(registry, inventory)
    if not admitted:
        assert selectors == ()
        return
    assert selectors == (CapacityClassSelector("self-hosted-default", _LABELS, "Build"),)
    matching = ObservedSelfHostedRunner(1, True, False, _LABELS)
    missing_label = ObservedSelfHostedRunner(2, True, False, ("linux", "self-hosted"))
    wrong_group = ObservedSelfHostedRunner(3, True, False, _LABELS)
    snapshot = project_runner_snapshot(
        selectors=selectors,
        repository_runners=(matching, missing_label, wrong_group),
        groups=(
            VisibleRunnerGroup(1, "Build", False, (matching, missing_label)),
            VisibleRunnerGroup(2, "Other", False, (wrong_group,)),
        ),
        observed_at=datetime(2026, 9, 25, tzinfo=UTC),
        freshness_ttl_seconds=15,
    )
    assert snapshot is not None
    assert snapshot.free_slots_for("self-hosted-default") == 1
