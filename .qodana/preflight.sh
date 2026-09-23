#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1

mise exec -- backend/.venv/bin/python -B - <<'PY'
import platform
import sys
import tomllib
from pathlib import Path

from scripts.dev_environment.environment import admit_dependencies, dependency_lease
from scripts.dev_environment.identity import derive_instance_identity

root = Path.cwd()
tools = tomllib.loads((root / "mise.toml").read_text())["tools"]
if platform.python_version() != tools["python"]:
    raise SystemExit("Wrong Python version; run mise run install")
if Path(sys.prefix) != root / "backend/.venv":
    raise SystemExit("Expected the project backend/.venv interpreter")
identity = derive_instance_identity(root)
with dependency_lease(identity):
    for scope in ("backend", "frontend"):
        admit_dependencies(identity, scope)
print("Qodana preflight: project dependency identities admitted; Python", platform.python_version())
PY

mise exec -- uv pip check --python backend/.venv/bin/python
mise exec -- node --input-type=module - <<'JS'
import fs from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";

const manifest = JSON.parse(fs.readFileSync("package.json", "utf8"));
const frontend = JSON.parse(fs.readFileSync("frontend/package.json", "utf8"));
const require = createRequire(resolve("frontend/package.json"));
const typescript = require("typescript/package.json");
if (process.versions.node !== manifest.engines.node) throw new Error("Wrong Node version");
if (typescript.version !== frontend.devDependencies.typescript.split("@").at(-1)) {
  throw new Error("Wrong workspace TypeScript version");
}
console.log("Qodana preflight: Node", process.versions.node, "TypeScript", typescript.version);
JS
