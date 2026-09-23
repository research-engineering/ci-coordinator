from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from ci_coordinator.consumer_contract_lab.node_executable import admit_node_executable

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("explicit_home", [False, True])
def test_installed_mise_resolves_native_node_in_isolated_environment(
    explicit_home: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    native_node = admit_node_executable(_ROOT, target)
    mise_candidate = shutil.which("mise")
    assert mise_candidate is not None
    mise = Path(mise_candidate).resolve(strict=True)
    assert not mise.samefile(native_node)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "mise").symlink_to(mise)
    (tools / "node").symlink_to(mise)
    home = Path.home()
    for name in tuple(os.environ):
        monkeypatch.delenv(name)
    monkeypatch.setenv("PATH", str(tools))
    if explicit_home:
        monkeypatch.setenv("HOME", str(home))

    assert admit_node_executable(_ROOT, target) == native_node
