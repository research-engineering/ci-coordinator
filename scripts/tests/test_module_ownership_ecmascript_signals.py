from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from scripts.module_ownership_candidates import (
    RepositoryPathSnapshot,
    fixture_candidate_inventory,
)
from scripts.module_ownership_profile import load_profile


def _candidates(root: Path, relative: str, source: str) -> list[dict[str, object]]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    snapshot = RepositoryPathSnapshot(
        deleted_tracked_count=0,
        paths=(relative,),
        tracked_count=1,
        untracked_count=0,
    )
    inventory = fixture_candidate_inventory(load_profile(), root, snapshot)
    return cast(list[dict[str, object]], inventory["candidates"])


def _metrics(row: dict[str, object]) -> dict[str, int | None]:
    return cast(dict[str, int | None], row["metricValues"])


def _reasons(row: dict[str, object]) -> list[str]:
    return cast(list[str], row["selectionReasons"])


def test_ecmascript_signals_use_the_declared_closed_grammar(tmp_path: Path) -> None:
    relative = "frontend/src/api/workbench/sample.ts"
    exports = "".join(f"export const public{index} = {index};\n" for index in range(13))
    imports = (
        'import "../providerInventory/schema";\n'
        'export { value } from "../workflowDiscovery/schema";\n'
        'import "../../components/button";'
    )

    rows = _candidates(tmp_path, relative, f"{exports}{imports}\n")

    assert len(rows) == 1
    assert rows[0]["metricValues"] == {
        "first-party-import-contexts": 3,
        "physical-lines": 16,
        "recognized-public-declarations": 14,
    }
    assert rows[0]["selectionReasons"] == [
        "recognized-public-declarations:gt:12",
        "first-party-import-contexts:gte:3",
    ]


def test_ecmascript_comments_are_excluded_and_multiline_exports_are_recognized(
    tmp_path: Path,
) -> None:
    relative = "frontend/src/api/workbench/comments.ts"
    comment = (
        "/*\n"
        + "".join(f'export const ignored{index} = "{index}";\n' for index in range(14))
        + 'import "../providerInventory/schema";\n*/\n'
    )
    admitted = "".join(f"export\nconst public{index} = {index};\n" for index in range(13))

    rows = _candidates(tmp_path, relative, comment + admitted)

    assert len(rows) == 1
    assert rows[0]["metricValues"] == {
        "first-party-import-contexts": 0,
        "physical-lines": 43,
        "recognized-public-declarations": 13,
    }
    assert rows[0]["selectionReasons"] == ["recognized-public-declarations:gt:12"]


@pytest.mark.parametrize(
    "source",
    (
        r"const pattern = /\/\*/;" + "\nexport const value = 1;\n",
        "const ratio = total / count;\nexport const value = 1;\n",
        "const value = `export const hidden = 1`;\n",
        "const value = 'continued\\\nstring';\n",
    ),
)
def test_ambiguous_ecmascript_lexemes_select_review_as_unknown(
    tmp_path: Path,
    source: str,
) -> None:
    rows = _candidates(tmp_path, "frontend/src/api/workbench/ambiguous.ts", source)

    assert len(rows) == 1
    assert _metrics(rows[0])["recognized-public-declarations"] is None
    assert _metrics(rows[0])["first-party-import-contexts"] is None
    assert _reasons(rows[0]) == [
        "recognized-public-declarations:unknown",
        "first-party-import-contexts:unknown",
    ]


@pytest.mark.parametrize("terminator", ("\n", "\r", "\r\n"))
def test_ecmascript_signals_normalize_line_terminators(
    tmp_path: Path,
    terminator: str,
) -> None:
    relative = "frontend/src/api/workbench/line-endings.ts"
    exports = terminator.join(
        f"export{terminator}const public{index} = {index};" for index in range(13)
    )
    imports = terminator.join(
        (
            'import "../providerInventory/schema";',
            'export { value } from "../workflowDiscovery/schema";',
            'import "../../components/button";',
        )
    )

    rows = _candidates(
        tmp_path,
        relative,
        f"{exports}{terminator}{imports}{terminator}",
    )

    assert len(rows) == 1
    assert _metrics(rows[0])["recognized-public-declarations"] == 14
    assert _metrics(rows[0])["first-party-import-contexts"] == 3


def test_long_ecmascript_imports_remain_within_the_bounded_grammar(
    tmp_path: Path,
) -> None:
    padding = " " * 5000
    imports = "\n".join(
        (
            f'import {{ value{padding}}} from "../providerInventory/schema";',
            f'import {{ value{padding}}} from "../workflowDiscovery/schema";',
            f'import {{ value{padding}}} from "../../components/button";',
        )
    )

    rows = _candidates(
        tmp_path,
        "frontend/src/api/workbench/long-imports.ts",
        f"{imports}\n",
    )

    assert len(rows) == 1
    assert _metrics(rows[0])["first-party-import-contexts"] == 3
    assert _reasons(rows[0]) == ["first-party-import-contexts:gte:3"]


def test_commonjs_signals_parse_static_requires_and_object_exports(
    tmp_path: Path,
) -> None:
    source = """\
const local = require("./local.cjs");
const provider = require("../../provider_inventory/ports.cjs");
const workflow = require("../../workflow_discovery/ports.cjs");
const pattern = /require\\(ignored\\)/u;
module.exports = Object.freeze({
  local,
  provider,
  workflow,
  named: 1,
});
"""

    rows = _candidates(
        tmp_path,
        "backend/src/ci_coordinator/target_artifacts/resources/sample.cjs",
        source,
    )

    assert len(rows) == 1
    assert _metrics(rows[0]) == {
        "first-party-import-contexts": 3,
        "physical-lines": 10,
        "recognized-public-declarations": 4,
    }
    assert _reasons(rows[0]) == ["first-party-import-contexts:gte:3"]


@pytest.mark.parametrize(
    "source",
    (
        "const dynamic = require(path);\n",
        "function load(require) { return require('./local.cjs'); }\n",
        "const require = createLoader();\nrequire('./local.cjs');\n",
        "const load = require;\n",
        "const current = module.exports;\n",
        "exports = replacement;\n",
        "const { exports } = namespace;\n",
        "module.exports = { known, ...dynamic };\n",
        "module.exports[computed] = value;\n",
        "Object.assign(module.exports, values);\n",
        "module.exports = { [computed]: value };\n",
        "module.exports = buildExports();\n",
        "module.exports = exportedSurface;\n",
        "module.exports = Object.assign({}, dynamic);\n",
        "module.exports = require('./other.cjs');\n",
        "module.exports = ;\n",
    ),
)
def test_unclosed_commonjs_forms_select_review_as_unknown(
    tmp_path: Path,
    source: str,
) -> None:
    rows = _candidates(
        tmp_path,
        "backend/src/ci_coordinator/target_artifacts/resources/unknown.cjs",
        source,
    )

    assert len(rows) == 1
    assert _metrics(rows[0])["recognized-public-declarations"] is None
    assert _metrics(rows[0])["first-party-import-contexts"] is None
    assert _reasons(rows[0]) == [
        "recognized-public-declarations:unknown",
        "first-party-import-contexts:unknown",
    ]


def test_commonjs_member_exports_are_counted(tmp_path: Path) -> None:
    exports = "".join(f"exports.public{index} = {index};\n" for index in range(13))

    rows = _candidates(
        tmp_path,
        "backend/src/ci_coordinator/target_artifacts/resources/exports.cjs",
        exports,
    )

    assert len(rows) == 1
    assert _metrics(rows[0])["recognized-public-declarations"] == 13
    assert _reasons(rows[0]) == ["recognized-public-declarations:gt:12"]
