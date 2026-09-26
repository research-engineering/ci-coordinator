from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.composer import Composer
from ruamel.yaml.constructor import DuplicateKeyError, SafeConstructor
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode

from ci_coordinator.repo_context import parse_workflow_capability, workflow_syntax

_PATH = ".github/workflows/check.yml"
_REVISION = "a" * 40


@dataclass(frozen=True)
class _Golden:
    directive: str
    environment: str
    value: dict[str, object]
    preimage: bytes
    digest: str


# Preimages are authored independently of the production parser/normalizer/serializer.
_GOLDENS = (
    _Golden(
        "",
        "{LIMIT: 1_000, BITS: 0b101}",
        {"LIMIT": 1000, "BITS": 5},
        b'{"job":{"env":{"BITS":5,"LIMIT":1000},"if":"always()","runs-on":"Linux"}}',
        "db108edabb10f61174ea178bb1b6bca186e49185651f600e4cbfe916abc58f5f",
    ),
    _Golden(
        "%YAML 1.2\n---\n",
        "{NUM: 052}",
        {"NUM": 52},
        b'{"job":{"env":{"NUM":52},"if":"always()","runs-on":"Linux"}}',
        "daee3161f44f7e6852ad221b39d2febc9eb4249b2edd294ab9d1b5099a5738c8",
    ),
    _Golden(
        "%YAML 1.1\n---\n",
        "{NUM: 052}",
        {"NUM": 42},
        b'{"job":{"env":{"NUM":42},"if":"always()","runs-on":"Linux"}}',
        "ae51f10963112276c59bd9978a0d014b304742d30ddd4c21e53ae20cf32249b7",
    ),
    _Golden(
        "%YAML 1.2\n---\n",
        '{NUM: !!str 052, FLAG: !!str true, NULLISH: "null"}',
        {"NUM": "052", "FLAG": "true", "NULLISH": "null"},
        b'{"job":{"env":{"FLAG":"true","NULLISH":"null","NUM":"052"},'
        b'"if":"always()","runs-on":"Linux"}}',
        "9e1adbb18b7769cda0df262cf01fbeda574ecf8bef6fa07788c68ef4af15e832",
    ),
    _Golden(
        "%YAML 1.2\n---\n",
        "{NEG: -2, ZERO: 0, FRACTION: -1.25, SCI: 1e2}",
        {"NEG": -2, "ZERO": 0, "FRACTION": -1.25, "SCI": 100.0},
        b'{"job":{"env":{"FRACTION":-1.25,"NEG":-2,"SCI":100,"ZERO":0},'
        b'"if":"always()","runs-on":"Linux"}}',
        "7f4fe43a95bdb6086f1d86ec1d34208f67858648b3fd5fba71aa6a87f1b5ec02",
    ),
    _Golden(
        "",
        "{<<: {NUM: 7}, BITS: 0b101}",
        {"NUM": 7, "BITS": 5},
        b'{"job":{"env":{"BITS":5,"NUM":7},"if":"always()","runs-on":"Linux"}}',
        "dfde4461df1700a3767d4adb33c2e70493dc6aa763e95a888c302cb10bf1efd3",
    ),
    _Golden(
        "",
        "&unused {LIMIT: 1_000}",
        {"LIMIT": 1000},
        b'{"job":{"env":{"LIMIT":1000},"if":"always()","runs-on":"Linux"}}',
        "6be02168226d6f1e6d7e45321d00a6a308bdcba2354529e953359452ae89deeb",
    ),
)


def _content(golden: _Golden) -> bytes:
    return (
        golden.directive
        + '"on": push\njobs:\n  test:\n    name: Test\n    if: "always()"\n'
        + f"    runs-on: Linux\n    env: {golden.environment}\n"
    ).encode()


def _plain_yaml() -> YAML:
    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    yaml.allow_duplicate_keys = False
    yaml.max_depth = 64
    assert yaml.Constructor is SafeConstructor
    return yaml


def _two_pass_reference(content: bytes) -> object:
    text = content.decode()
    root = _plain_yaml().compose(text)
    assert isinstance(root, MappingNode)
    workflow_syntax._validate_node_graph(root)
    loaded: object = _plain_yaml().load(text)
    return loaded


def _expected_mapping(digest: str) -> dict[str, object]:
    return {
        "path": _PATH,
        "jobIds": ["test"],
        "jobNeeds": [["test", []]],
        "providerJobNames": [["test", "Test"]],
        "alwaysJobIds": ["test"],
        "triggers": ["push"],
        "localReusableWorkflowPaths": [],
        "jobAuthorities": [
            {
                "jobId": "test",
                "controlProjectionHash": digest,
                "conditionContexts": [],
                "declaresContinueOnError": False,
            }
        ],
        "declaresWorkflowEnvironment": False,
        "declaresWorkflowDefaults": False,
        "jobRunnerSelectors": [{"jobId": "test", "selector": {"labels": ["linux"], "group": None}}],
    }


@pytest.mark.parametrize(
    "golden",
    _GOLDENS,
    ids=["extensions", "yaml12", "yaml11", "quoted-tagged", "numbers", "merge", "anchor"],
)
def test_literal_values_hashes_and_same_library_two_pass_parity(
    golden: _Golden,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _content(golden)
    expected = {
        "on": "push",
        "jobs": {
            "test": {
                "name": "Test",
                "if": "always()",
                "runs-on": "Linux",
                "env": golden.value,
            }
        },
    }
    reference = _two_pass_reference(content)
    assert reference == expected
    assert sha256(golden.preimage).hexdigest() == golden.digest
    assert json.loads(golden.preimage)["job"]["env"] == golden.value
    constructed: list[object] = []
    construct = SafeConstructor.construct_document
    registry = dict(SafeConstructor.yaml_constructors)

    def observe(constructor: SafeConstructor, node: Node) -> object:
        value: object = construct(constructor, node)
        constructed.append(value)
        return value

    monkeypatch.setattr(SafeConstructor, "construct_document", observe)
    capability = parse_workflow_capability(content, path=_PATH, revision_sha=_REVISION)
    assert capability is not None
    assert constructed == [expected]
    for document in (reference, constructed[0]):
        assert type(document) is dict
        actual_environment = document["jobs"]["test"]["env"]
        assert type(actual_environment) is dict
        assert {key: type(value) for key, value in actual_environment.items()} == {
            key: type(value) for key, value in golden.value.items()
        }
    assert capability.to_stable_mapping() == _expected_mapping(golden.digest)
    assert capability.to_identity_mapping() == {
        "revision": _REVISION,
        **_expected_mapping(golden.digest),
    }
    assert SafeConstructor.yaml_constructors == registry


def test_actual_composer_runs_once_while_old_positive_reference_runs_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    golden = _GOLDENS[0]
    content = _content(golden)
    compose = Composer.compose_document
    calls: list[Composer] = []

    def counted(composer: Composer) -> Node:
        calls.append(composer)
        result: Node = compose(composer)
        return result

    monkeypatch.setattr(Composer, "compose_document", counted)
    reference = _two_pass_reference(content)
    assert type(reference) is dict and reference["jobs"]["test"]["env"] == golden.value
    assert len(calls) == 2 and calls[0] is not calls[1]
    calls.clear()
    capability = parse_workflow_capability(content, path=_PATH, revision_sha=_REVISION)
    assert capability is not None
    assert capability.to_stable_mapping() == _expected_mapping(golden.digest)
    assert len(calls) == 1


def _field(node: Node, name: str) -> Node:
    assert isinstance(node, MappingNode)
    matches = [value for key, value in node.value if key.value == name]
    assert len(matches) == 1
    value: object = matches[0]
    assert isinstance(value, Node)
    return value


def test_lexical_projection_precedes_real_mapping_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    golden = _GOLDENS[5]
    content = _content(golden)
    assert type(_two_pass_reference(content)) is dict
    construct = SafeConstructor.construct_document
    calls: list[bool] = []

    def mutate(constructor: SafeConstructor, node: Node) -> object:
        job = _field(_field(node, "jobs"), "test")
        name = _field(job, "name")
        assert isinstance(name, ScalarNode) and name.value == "Test"
        environment = _field(job, "env")
        assert isinstance(environment, MappingNode)
        assert any(key.tag == "tag:yaml.org,2002:merge" for key, _ in environment.value)
        # A cosmetic sentinel detects any lexical reread after safe construction starts.
        name.value = "mutated by constructor"
        value: object = construct(constructor, node)
        assert all(key.tag != "tag:yaml.org,2002:merge" for key, _ in environment.value)
        calls.append(True)
        return value

    monkeypatch.setattr(SafeConstructor, "construct_document", mutate)
    capability = parse_workflow_capability(content, path=_PATH, revision_sha=_REVISION)
    assert capability is not None and calls == [True]
    assert capability.to_stable_mapping() == _expected_mapping(golden.digest)


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        (b"    extra: {a: &v scalar, b: *v}\n", "workflow aliases are not admitted"),
        (b"    <<: {continue-on-error: true}\n", "workflow mapping keys must be strings"),
        (b"    =: harmless\n", "workflow mapping keys must be strings"),
        (b"<<: {env: {ROOT: literal}}\n", "workflow mapping keys must be strings"),
        (b"metadata: !custom value\n", "workflow contains a custom YAML tag"),
    ],
    ids=["alias", "job-merge", "value-key", "root-merge", "custom-tag"],
)
def test_original_graph_and_lexical_guards_precede_construction(
    extra: bytes,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _content(_GOLDENS[0])
    assert parse_workflow_capability(baseline, path=_PATH, revision_sha=_REVISION) is not None
    errors: list[str] = []
    project = workflow_syntax._workflow_node_projection

    def observed(root: Node) -> workflow_syntax._WorkflowNodeProjection:
        try:
            return project(root)
        except ValueError as error:
            errors.append(str(error))
            raise

    def forbidden(*_args: object, **_kwargs: object) -> object:
        pytest.fail("invalid original nodes must not reach safe construction")

    monkeypatch.setattr(workflow_syntax, "_workflow_node_projection", observed)
    monkeypatch.setattr(SafeConstructor, "construct_document", forbidden)
    assert parse_workflow_capability(baseline + extra, path=_PATH, revision_sha=_REVISION) is None
    assert errors == [reason]


def test_duplicates_in_ignored_subtrees_still_reach_safe_constructor_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _content(_GOLDENS[0])
    assert parse_workflow_capability(baseline, path=_PATH, revision_sha=_REVISION) is not None
    content = baseline + b"metadata: {duplicate: 1, duplicate: 2}\n"
    with pytest.raises(DuplicateKeyError):
        _two_pass_reference(content)
    construct = SafeConstructor.construct_document
    rejected: list[bool] = []

    def observed(constructor: SafeConstructor, node: Node) -> object:
        try:
            result: object = construct(constructor, node)
        except DuplicateKeyError:
            rejected.append(True)
            raise
        return result

    monkeypatch.setattr(SafeConstructor, "construct_document", observed)
    assert parse_workflow_capability(content, path=_PATH, revision_sha=_REVISION) is None
    assert rejected == [True]


def test_byte_boundary_precedes_yaml_but_keeps_a_successful_at_limit_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _content(_GOLDENS[0])
    maximum = workflow_syntax.MAX_WORKFLOW_FILE_BYTES
    at_limit = baseline + b"#" + b" " * (maximum - len(baseline) - 2) + b"\n"
    assert len(at_limit) == maximum
    capability = parse_workflow_capability(at_limit, path=_PATH, revision_sha=_REVISION)
    assert capability is not None
    assert capability.to_stable_mapping() == _expected_mapping(_GOLDENS[0].digest)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        pytest.fail("oversized bytes must not construct a YAML instance")

    monkeypatch.setattr(workflow_syntax, "YAML", forbidden)
    assert parse_workflow_capability(at_limit + b"\n", path=_PATH, revision_sha=_REVISION) is None


def test_all_lexical_fields_and_constructed_root_controls_have_literal_identity() -> None:
    content = b"""on: [workflow_dispatch, push]
env: {TOP: literal}
defaults: {run: {shell: bash}}
jobs:
  test:
    name: Test
    if: '${{ always() }}'
    runs-on: {group: Build, labels: [Linux, X64]}
    continue-on-error: false
    steps:
      - name: Cosmetic
        run: echo ok
        continue-on-error: false
  final:
    name: Final
    needs: test
    if: "${{ needs.test.result == 'success' }}"
    uses: ./.github/workflows/child.yml
    secrets: {}
"""
    test_preimage = (
        b'{"job":{"continue-on-error":false,"if":"${{ always() }}",'
        b'"runs-on":{"group":"Build","labels":["Linux","X64"]},'
        b'"steps":[{"continue-on-error":false,"run":"echo ok"}]},'
        b'"workflowDefaults":{"run":{"shell":"bash"}},'
        b'"workflowEnvironment":{"TOP":"literal"}}'
    )
    final_preimage = (
        b'{"job":{"if":"${{ needs.test.result == \'success\' }}","secrets":{},'
        b'"uses":"./.github/workflows/child.yml"},'
        b'"workflowDefaults":{"run":{"shell":"bash"}},'
        b'"workflowEnvironment":{"TOP":"literal"}}'
    )
    test_hash = "08b1fb2c20fc9d3cc474e94567ae9e8fe4ef0b4110bfb6e14c3448a809da62e2"
    final_hash = "34736a31e41c77a9a13de05d45c58941bff780f6c0c0e3d81bb5110599bf531f"
    assert sha256(test_preimage).hexdigest() == test_hash
    assert sha256(final_preimage).hexdigest() == final_hash
    reference = _two_pass_reference(content)
    assert type(reference) is dict
    assert reference["env"] == {"TOP": "literal"}
    assert reference["defaults"] == {"run": {"shell": "bash"}}
    capability = parse_workflow_capability(content, path=_PATH, revision_sha=_REVISION)
    assert capability is not None
    expected = {
        "path": _PATH,
        "jobIds": ["final", "test"],
        "jobNeeds": [["final", ["test"]], ["test", []]],
        "providerJobNames": [["test", "Test"]],
        "alwaysJobIds": ["test"],
        "triggers": ["push", "workflow_dispatch"],
        "localReusableWorkflowPaths": [".github/workflows/child.yml"],
        "jobAuthorities": [
            {
                "jobId": "final",
                "controlProjectionHash": final_hash,
                "conditionContexts": ["needs"],
                "declaresContinueOnError": False,
            },
            {
                "jobId": "test",
                "controlProjectionHash": test_hash,
                "conditionContexts": [],
                "declaresContinueOnError": True,
            },
        ],
        "declaresWorkflowEnvironment": True,
        "declaresWorkflowDefaults": True,
        "jobRunnerSelectors": [
            {"jobId": "test", "selector": {"labels": ["linux", "x64"], "group": "Build"}}
        ],
    }
    assert capability.to_stable_mapping() == expected
    assert capability.to_identity_mapping() == {"revision": _REVISION, **expected}
