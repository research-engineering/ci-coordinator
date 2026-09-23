from __future__ import annotations

import importlib.util
import json
import runpy
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def comparison() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "runtime_compare", ROOT / "docker/runtime/compare.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    root = tmp_path / "repository"
    root.mkdir()
    monkeypatch.chdir(root)
    for args in (
        ["init", "-q"],
        ["config", "user.name", "Example"],
        ["config", "user.email", "example@example.test"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", *args], check=True, capture_output=True, timeout=10)
    path = root / "backend/src/ci_coordinator/runtime/__init__.py"
    path.parent.mkdir(parents=True)
    path.write_text("baseline = True\n")
    (root / "Dockerfile").write_text("FROM scratch\n")
    subprocess.run(["git", "add", "."], check=True, timeout=10)
    subprocess.run(["git", "commit", "-qm", "fixture"], check=True, timeout=10)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=10).strip()
    return root, commit


@pytest.mark.parametrize("value", ["HEAD", "master", "0" * 40, "a" * 39, "A" * 40, "--help", ""])
def test_comparison_rejects_nonexact_baseline(comparison: ModuleType, value: str) -> None:
    with pytest.raises(ValueError, match="exact nonzero commit"):
        comparison.admitted_commit(value)


@pytest.mark.parametrize("failure", [False, True])
def test_baseline_uses_whole_committed_tree_and_cleans_it(
    comparison: ModuleType,
    repository: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    root, commit = repository
    original = root / "backend/src/ci_coordinator/runtime/__init__.py"
    original.write_text("candidate = True\n")
    real_run = subprocess.run
    contexts: list[Path] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if argv[0] != "docker":
            return real_run(argv, **kwargs)
        context = Path(argv[-1])
        contexts.append(context)
        assert context != root
        assert (context / original.relative_to(root)).read_text() == "baseline = True\n"
        assert (context / "Dockerfile").read_text() == "FROM scratch\n"
        assert "CI_COORDINATOR_SOURCE_COMMIT=" + commit in argv
        assert original.read_text() == "candidate = True\n"
        if failure:
            raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert comparison.admitted_commit(commit) == commit
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            comparison.build_baseline(commit, "baseline")
    else:
        comparison.build_baseline(commit, "baseline")
    assert len(contexts) == 1 and not contexts[0].exists()
    assert original.read_text() == "candidate = True\n"


@pytest.mark.parametrize("baseline", ["", "0" * 40])
def test_missing_baseline_is_not_measurement(
    comparison: ModuleType,
    repository: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    baseline: str,
) -> None:
    root, commit = repository
    (root / "qualification").mkdir()
    monkeypatch.setenv("SOURCE_COMMIT", commit)
    monkeypatch.setenv("BASELINE_COMMIT", baseline)
    monkeypatch.setenv("RUNNER_TEMP", str(root))
    comparison.main()
    report = json.loads((root / "qualification/performance.json").read_bytes())
    assert report["status"] == "not-measured"
    assert report["reason"] == "baseline-unavailable"
    assert report["results"] == {}
    assert report["candidateSource"] == commit


@pytest.mark.parametrize("mismatch", [None, "payload", "counters"])
def test_comparison_binds_alternating_samples_and_rejects_incomparable_results(
    comparison: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str | None,
) -> None:
    calls: list[str] = []
    cleaned: list[list[str]] = []
    script = b"owned benchmark"
    identities = {"candidate": "sha256:" + "a" * 64, "baseline": "sha256:" + "b" * 64}

    def output(argv: list[str], **kwargs: Any) -> str | bytes:
        if argv[:3] == ["docker", "image", "inspect"]:
            return identities[argv[3]] + "\n"
        role = "candidate" if argv[-4] == identities["candidate"] else "baseline"
        calls.append(role)
        assert kwargs["input"] == script
        assert kwargs["timeout"] == 30
        assert "--read-only" in argv and "--cap-drop" in argv
        assert argv[argv.index("--network") + 1] == "none"
        value = 10 if role == "candidate" else 20
        return json.dumps(
            {
                "payloadSha256": "wrong"
                if mismatch == "payload" and role == "baseline"
                else "c" * 64,
                "importNs": value,
                "maxRssKiB": 100,
                "elapsedNs": {"validation": value},
                "cpuNs": {} if mismatch == "counters" else {"validation": value // 2},
            }
        ).encode()

    def cleanup(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        cleaned.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "check_output", output)
    monkeypatch.setattr(subprocess, "run", cleanup)
    if mismatch:
        with pytest.raises(ValueError, match="disagree"):
            comparison.compare("candidate", "baseline", script)
    else:
        result = comparison.compare("candidate", "baseline", script)
        assert result["candidate"]["medianElapsedNs"] == {"validation": 10}
        assert result["baseline"]["medianCpuNs"] == {"validation": 10}
        assert result["candidate"]["imageId"] == identities["candidate"]
    assert calls == [
        role
        for sample in range(5)
        for role in ["candidate", "baseline"][:: 1 if sample % 2 == 0 else -1]
    ]
    assert cleaned == [["docker", "rm", "--force", "runtime-benchmark"]]


@pytest.mark.parametrize("failure", [None, "build", "interrupt"])
def test_layer_probe_never_modifies_checkout(
    repository: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    root, commit = repository
    target = root / "backend/src/ci_coordinator/runtime/__init__.py"
    original = target.read_bytes()
    (root / "qualification").mkdir()
    monkeypatch.setenv("SOURCE_COMMIT", commit)
    monkeypatch.setenv("RUNNER_TEMP", str(root))
    monkeypatch.setenv("IMAGE", "candidate")
    real_run = subprocess.run
    contexts: list[Path] = []

    def inspect(argv: list[str], **kwargs: Any) -> bytes:
        assert argv[:3] == ["docker", "image", "inspect"]
        tail = "probe" if argv[3].endswith("application-layer-probe") else "application"
        return json.dumps(
            [{"Id": argv[3], "RootFS": {"Layers": ["runtime", "deps", tail]}}]
        ).encode()

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if argv[0] != "docker":
            return real_run(argv, **kwargs)
        context = Path(argv[-1])
        contexts.append(context)
        assert target.read_bytes() == original
        assert (context / target.relative_to(root)).read_bytes().startswith(original)
        assert (context / target.relative_to(root)).read_bytes() != original
        if failure == "build":
            raise subprocess.CalledProcessError(1, argv)
        if failure == "interrupt":
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "check_output", inspect)
    monkeypatch.setattr(subprocess, "run", run)
    if failure:
        with pytest.raises(
            KeyboardInterrupt if failure == "interrupt" else subprocess.CalledProcessError
        ):
            runpy.run_path(str(ROOT / "docker/runtime/check_layer_reuse.py"))
    else:
        runpy.run_path(str(ROOT / "docker/runtime/check_layer_reuse.py"))
        assert json.loads((root / "qualification/layer-reuse.json").read_bytes())[
            "unchangedLayers"
        ] == ["runtime", "deps"]
    assert target.read_bytes() == original
    assert len(contexts) == 1 and not contexts[0].exists()
