from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ci_coordinator.consumer_contract_lab import node_executable
from ci_coordinator.consumer_contract_lab.node_executable import (
    NodeExecutableError,
    admit_node_executable,
)
from ci_coordinator.consumer_contract_lab.process import BoundedProcessResult

_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def tool_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    tools = tmp_path / "tools"
    coordinator = tmp_path / "coordinator"
    target = tmp_path / "target"
    for directory in (tools, coordinator, target):
        directory.mkdir()
    node = tools / "node"
    node.write_bytes(b"")
    node.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools))
    return node, coordinator, target


@pytest.mark.parametrize("unrelated_mise", [False, True])
def test_native_resolution_scrubs_preloads_and_credentials(
    unrelated_mise: bool,
    tool_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node, coordinator, target = tool_paths
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("NODE_OPTIONS", "--require=/untrusted/preload.cjs")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-cross")
    if unrelated_mise:
        mise = target / "mise"
        mise.write_bytes(b"")
        mise.chmod(0o700)
        node.with_name("mise").symlink_to(mise)

    def query(command: str, args: tuple[str, ...], **kwargs: Any) -> BoundedProcessResult:
        assert command == str(node)
        assert args == (
            "--eval",
            "process.stdout.write(JSON.stringify([process.version,process.execPath]))",
        )
        calls.append(kwargs)
        return BoundedProcessResult(0, json.dumps(["v24.21.0", str(node)]).encode(), b"")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(node_executable, "run_bounded", query)
    assert admit_node_executable(coordinator, target) == str(node)
    assert len(calls) == 1
    assert calls[0]["env"] == {"LANG": "C", "LC_ALL": "C"}
    assert calls[0]["cwd"] == coordinator
    assert 0 < calls[0]["timeout_seconds"] <= 10
    assert calls[0]["max_output_bytes"] == 8_192


@pytest.mark.parametrize(
    "result",
    [
        BoundedProcessResult(1, b"", b""),
        BoundedProcessResult(0, b"{}", b""),
        BoundedProcessResult(0, b'["v24.19.0","/node"]', b""),
        BoundedProcessResult(0, b'["v24.21.0","/another/node"]', b""),
        BoundedProcessResult(0, b"not-json", b""),
        BoundedProcessResult(0, b"[]", b"warning"),
        BoundedProcessResult(None, b"", b"", "process timeout"),
    ],
)
def test_rejects_unadmitted_runtime_identity(
    result: BoundedProcessResult,
    tool_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, coordinator, target = tool_paths
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(node_executable, "run_bounded", lambda *_a, **_kw: result)
    with pytest.raises(NodeExecutableError):
        admit_node_executable(coordinator, target)


def test_version_mismatch_is_rejected_with_the_correct_executable_path(
    tool_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node, coordinator, target = tool_paths
    result = BoundedProcessResult(0, json.dumps(["v24.19.0", str(node)]).encode(), b"")
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(node_executable, "run_bounded", lambda *_a, **_kw: result)
    with pytest.raises(NodeExecutableError, match="identity is not admitted"):
        admit_node_executable(coordinator, target)


@pytest.mark.parametrize(
    "kind", ["missing", "relative", "target", "symlink", "not-executable", "missing-target"]
)
def test_unsafe_path_is_rejected_before_process_execution(
    kind: str,
    tool_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node, coordinator, target = tool_paths
    if kind == "missing":
        node.unlink()
    elif kind == "relative":
        monkeypatch.chdir(node.parent)
        monkeypatch.setenv("PATH", ".")
    elif kind == "target":
        node.rename(target / "node")
        monkeypatch.setenv("PATH", str(target))
    elif kind == "symlink":
        node.rename(target / "node")
        node.symlink_to(target / "node")
    elif kind == "not-executable":
        node.chmod(0o600)
    else:
        target.rmdir()

    def forbidden(*_args: object, **_kwargs: object) -> BoundedProcessResult:
        pytest.fail("unadmitted tool was executed")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(node_executable, "run_bounded", forbidden)
    with pytest.raises(NodeExecutableError):
        admit_node_executable(coordinator, target)


@pytest.mark.parametrize("explicit_home", [False, True])
def test_contextual_mise_shim_resolves_real_node_without_executing_the_shim(
    explicit_home: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    real_node = admit_node_executable(_ROOT, target)
    tools = tmp_path / "tools"
    tools.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    mise = tools / "mise"
    mise.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\n"
        "from pathlib import Path\n"
        "assert Path(sys.argv[0]).name == 'mise'\n"
        "assert sys.argv[1:] == ['which','node','--tool','core:node@24.21.0']\n"
        f"assert Path.cwd() == Path({_ROOT.as_posix()!r})\n"
        f"assert os.environ['HOME'] == {str(home)!r}\n"
        "assert not {'NODE_OPTIONS','GITHUB_TOKEN','PATH'} & os.environ.keys()\n"
        f"print({real_node!r})\n",
    )
    mise.chmod(0o700)
    (tools / "node").symlink_to(mise)
    monkeypatch.setenv("PATH", str(tools))
    if explicit_home:
        monkeypatch.setenv("HOME", str(home))
    else:
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setenv("NODE_OPTIONS", "--require=/missing/preload.cjs")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-cross")

    assert admit_node_executable(_ROOT, target) == real_node


@pytest.mark.parametrize(
    "setting",
    [
        "HOME",
        "MISE_DATA_DIR",
        "MISE_CONFIG_DIR",
        "MISE_CACHE_DIR",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
    ],
)
def test_tool_directory_cannot_delegate_configuration_to_target(
    setting: str,
    tool_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node, coordinator, target = tool_paths
    node.rename(node.with_name("mise"))
    node.symlink_to(node.with_name("mise"))
    monkeypatch.setenv(setting, str(target))

    def forbidden(*_args: object, **_kwargs: object) -> BoundedProcessResult:
        pytest.fail("target-owned tool configuration was used")

    monkeypatch.setattr(node_executable, "run_bounded", forbidden)
    with pytest.raises(NodeExecutableError, match="outside target authority"):
        admit_node_executable(coordinator, target)


@pytest.mark.parametrize("raw", [b"", b"node\n", b"/node\nextra\n", b"\xff\n"])
def test_mise_response_cannot_supply_an_ambiguous_executable(
    raw: bytes,
    tool_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node, coordinator, target = tool_paths
    node.rename(node.with_name("mise"))
    node.symlink_to(node.with_name("mise"))
    monkeypatch.setattr(
        node_executable,
        "run_bounded",
        lambda *_a, **_kw: BoundedProcessResult(0, raw, b""),
    )
    with pytest.raises(NodeExecutableError):
        admit_node_executable(coordinator, target)


def test_admission_uses_one_deadline_across_mise_and_native_probe(
    tool_paths: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node, coordinator, target = tool_paths
    mise = node.with_name("mise")
    mise.write_bytes(b"")
    mise.chmod(0o700)
    node.unlink()
    node.symlink_to(mise)
    clock = iter([1.0, 2.0, 3.0, 12.0])
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(node_executable, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    calls = 0

    def query(*_args: object, **kwargs: Any) -> BoundedProcessResult:
        nonlocal calls
        calls += 1
        assert kwargs["timeout_seconds"] == 9
        return BoundedProcessResult(0, f"{mise}\n".encode(), b"")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(node_executable, "run_bounded", query)
    with pytest.raises(NodeExecutableError, match="deadline expired"):
        admit_node_executable(coordinator, target)
    assert calls == 1
