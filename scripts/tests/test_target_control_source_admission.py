from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from scripts.bounded_process import spawn
from scripts.target_control_bundle import (
    ESBUILD_VERSION,
    REPO_ROOT,
    SOURCE_ROOT,
    TargetControlBundleError,
    _build_environment,
    render_bundle,
)
from scripts.target_control_source_admission import (
    SourceModuleDependencies,
    TargetControlSourceAdmissionError,
    admit_source_dependency_graph,
)

ALLOWED_EXTERNAL_IMPORTS = frozenset({"node:crypto", "node:fs", "node:util", "node:zlib"})


def test_current_source_has_the_exact_admitted_dependency_graph() -> None:
    graph = admit_source_dependency_graph(
        _source_snapshot(SOURCE_ROOT),
        allowed_external_imports=ALLOWED_EXTERNAL_IMPORTS,
    )

    assert graph.modules == (
        SourceModuleDependencies("consume_plan.cjs", (), ("node:fs", "node:zlib")),
        SourceModuleDependencies(
            "main.cjs",
            ("consume_plan.cjs", "validate_gate.cjs", "validate_plan.cjs"),
            (),
        ),
        SourceModuleDependencies(
            "validate_gate.cjs",
            ("validation_core.cjs", "validation_execution.cjs"),
            (),
        ),
        SourceModuleDependencies(
            "validate_plan.cjs",
            (
                "validation_core.cjs",
                "validation_envelope.cjs",
                "validation_execution.cjs",
            ),
            (),
        ),
        SourceModuleDependencies(
            "validation_core.cjs",
            (),
            ("node:crypto", "node:fs", "node:util"),
        ),
        SourceModuleDependencies(
            "validation_envelope.cjs",
            ("validation_core.cjs",),
            ("node:crypto",),
        ),
        SourceModuleDependencies(
            "validation_execution.cjs",
            ("validation_core.cjs", "validation_envelope.cjs"),
            (),
        ),
    )


@pytest.mark.parametrize(
    ("statement", "message"),
    (
        ('const name = "node:child_process"; require(name);', "string literal"),
        ('require?.("node:fs");', "require call syntax is not exact"),
        ('require ("node:fs");', "require call syntax is not exact"),
        ("require();", "exactly one argument"),
        ('require("node:fs", "node:util");', "exactly one argument"),
        ("require(`node:fs`);", "string literal"),
        ('require("node:\\x66s");', "plain literal"),
        ("const load = require;", "indirect or alternate require"),
        ('require.resolve("node:fs");', "indirect or alternate require"),
        ('module.require("node:fs");', "module may only publish exact exports"),
        ('import("node:fs");', "ESM or dynamic import"),
        ('import fs from "node:fs";', "ESM or dynamic import"),
        ('process.getBuiltinModule("node:fs");', "process capability is not admitted"),
        ('arguments[1]("node:http");', "ambient loader or dynamic-code"),
        (r'arg\u0075ments[1]("node:http");', "escaped identifier"),
        (r'requ\u0069re("node:http");', "escaped identifier"),
        ('globalThis.process.getBuiltinModule("node:fs");', "ambient loader or dynamic-code"),
        ('eval("1");', "ambient loader or dynamic-code"),
        ('Function("return 1");', "ambient loader or dynamic-code"),
        ('require("node:child_process");', "external require is not admitted"),
        ('require("./unknown.cjs");', "outside the source set"),
        ('require("./consume_plan.cjs");', "duplicate source dependency"),
        ("const =", "parse error"),
    ),
)
def test_source_admission_rejects_unclosed_module_loading_forms(
    statement: str,
    message: str,
) -> None:
    sources = _source_snapshot(SOURCE_ROOT)
    sources["main.cjs"] += f"\n{statement}\n".encode()

    with pytest.raises(TargetControlSourceAdmissionError, match=message):
        admit_source_dependency_graph(
            sources,
            allowed_external_imports=ALLOWED_EXTERNAL_IMPORTS,
        )


def test_source_admission_rejects_an_empty_inventory() -> None:
    with pytest.raises(TargetControlSourceAdmissionError, match="source set is empty"):
        admit_source_dependency_graph({}, allowed_external_imports=ALLOWED_EXTERNAL_IMPORTS)


@pytest.mark.parametrize(
    ("loader", "admission_error"),
    (
        (b'arguments[1]("node:http");', "ambient loader or dynamic-code"),
        (b'arg\\u0075ments[1]("node:http");', "escaped identifier"),
    ),
)
def test_real_esbuild_omits_commonjs_loader_alias_but_source_admission_rejects_it(
    tmp_path: Path,
    loader: bytes,
    admission_error: str,
) -> None:
    esbuild = REPO_ROOT / "node_modules" / ".bin" / "esbuild"
    if not esbuild.is_file():
        pytest.skip("repository-quality owns the installed real-esbuild falsifier")
    source_root = tmp_path / "source"
    build_root = tmp_path / "build"
    shutil.copytree(SOURCE_ROOT, source_root)
    build_root.mkdir()
    main = source_root / "main.cjs"
    main.write_bytes(main.read_bytes() + b"\n" + loader + b"\n")

    version = spawn(
        str(esbuild),
        ("--version",),
        cwd=source_root,
        env=_build_environment(str(esbuild)),
        max_buffer=128,
        timeout_seconds=60,
    )
    assert version.status == 0
    assert version.stdout == f"{ESBUILD_VERSION}\n"
    completed = spawn(
        str(esbuild),
        (
            "main.cjs",
            "--bundle",
            "--platform=node",
            "--format=cjs",
            "--target=node24",
            "--charset=utf8",
            "--legal-comments=none",
            "--log-level=warning",
            f"--metafile={build_root / 'metafile.json'}",
            f"--outfile={build_root / 'bundle.cjs'}",
        ),
        cwd=source_root,
        env=_build_environment(str(esbuild)),
        max_buffer=65_536,
        timeout_seconds=60,
    )
    assert completed.status == 0
    assert completed.error is None
    metafile = json.loads((build_root / "metafile.json").read_text(encoding="utf-8"))
    observed = {
        imported["path"] for source in metafile["inputs"].values() for imported in source["imports"]
    }
    assert "node:http" not in observed

    with pytest.raises(TargetControlBundleError, match=admission_error):
        render_bundle(source_root=source_root, esbuild=str(esbuild))


def _source_snapshot(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(root.glob("*.cjs"))}
