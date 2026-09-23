# Configure JetBrains Inspections

Status: local IDE setup and Qodana qualification procedure

## Prepare The Existing Environment

Run from the repository root:

```sh
mise trust
mise run install
sh .qodana/preflight.sh
mise which uv
mise which node
mise which pnpm
```

Installation uses the existing toolchain and dependency locks. The preflight
checks the native dependency ownership markers, Python and Node versions,
Python dependency consistency, and the workspace TypeScript package. It does
not install packages, prove every installed file matches its original artifact,
or hold an environment lease for the subsequent IDE scan. Do not synchronize
dependencies during analysis. A failed preflight is not a clean inspection run.

## Configure IntelliJ IDEA Or PyCharm

In IntelliJ, enable the Python and JavaScript/TypeScript plugins. Open the
repository root, not its parent directory. Use one Python module for the
shared backend and repository-script environment; additional modules require
an actual independent environment or import-model need.

1. Add the existing `backend/.venv/bin/python` interpreter. Keep the virtual
   environment path, even when it is a symlink to a mise-managed executable.
2. Assign that Python SDK to the project and Python module. Adding an SDK to
   the global list alone does not assign it. Do not inherit a Java SDK.
3. Keep the content root at the repository root. Source roots are the root
   itself and `backend/src`; `backend/tests/unit` is the additional test import
   root declared by pytest. Keep migrations, other tests, and scripts in scope.
4. Exclude virtual environments, dependency directories, caches, and build
   output from project sources, while retaining library resolution.
5. Configure the uv executable using the concrete path returned by `mise which
   uv`. Do not assume a GUI subprocess resolves a mise shim in this project.
   Prefer the IDE's existing-uv-environment integration. A missing pip module
   is not evidence that a uv environment is broken; do not install or upgrade
   dependencies through the IDE independently of the repository locks.
6. In JavaScript Runtime settings, use the Node and pnpm executables returned
   by mise. In TypeScript settings, select `frontend/node_modules/typescript`.
   This package is distinct from the separately declared native compiler.
7. Verify the resolved Python version, imports, Node version, TypeScript
   package, and content roots in the IDE before accepting its diagnostics.

Local `.idea` and `.iml` files are ignored because they contain machine-specific
SDK mappings. Do not commit absolute personal interpreter paths. PyCharm may
expose the same settings under different labels; it does not remove the need
to qualify the selected environment.

## Run Qodana Without Applying Fixes

The root `qodana.yaml` selects the Python linter release line, the recommended
profile plus Python and JavaScript/TypeScript categories, and only dependency,
cache, IDE-metadata, and generated build-output exclusions. Tests, migrations,
repository scripts, and tracked generated contracts remain in scope. No
baseline, fix application, or severity suppression is configured.

For the installed IDE, first run the preflight manually. Then use
**Tools > Qodana > Try Code Analysis with Qodana** with the checked-in YAML.
Leave cloud upload and automatic fixes disabled. The IDE run uses its installed
engine and plugins; the YAML linter does not replace that engine.

A standalone native run requires an installed compatible Qodana CLI and the
appropriate Qodana license/token, separately from the IDE license:

```sh
export QODANA_PYTHON_PATH="$PWD/backend/.venv/bin/python"
sh .qodana/preflight.sh
qodana scan --within-docker false
```

The configured `2026.2` tag is a release-line pin, not an immutable image
digest. Before making CI reproducibility claims, qualify and pin the exact
engine build or platform-specific image digest and plugin set. Do not reuse a
macOS virtual environment in Linux: provision the locked dependencies in the
selected runner before scanning. No cloud publication or CI workflow is
enabled by this procedure.

## Admit A Report

Record the source revision and dirty-file inventory, exact engine/plugin
versions, profile, environment, analyzed paths, exclusions, and report path.
Check the report's SDK and module inventory and sanity results. Successful
process termination or fewer findings does not prove a correct environment.

Inspect the IDE log for the exact scan interval as well. An internal analyzer
exception can coexist with `executionSuccessful=true` in SARIF. Retain the
exception and affected language as unresolved until an independent checker or
a qualified engine repair establishes the relevant diagnostic boundary. A
rerun without the exception only proves non-reproduction for that run; do not
disable inspections or internal consistency checks to make the log clean.

Compare environment-only reruns on unchanged application source. Treat each
remaining finding as a hypothesis: identify the applicable contract and a
reachable counterexample, challenge false positives, and require a focused
regression witness before changing behavior. Unknown cases stay unresolved.

Qodana complements the repository's Ruff, mypy, TypeScript, Biome, API-contract,
security, and behavioral checks; it does not replace them. Follow the current
repository execution policy for CI-only behavioral and full-suite witnesses.
Neither a clean report nor this procedure proves absence of all defects.

## References

- [Python SDKs and uv in IntelliJ](https://www.jetbrains.com/help/idea/configuring-python-sdk.html)
- [Qodana IDE integration](https://www.jetbrains.com/help/qodana/qodana-ide-plugin.html)
- [Qodana inspection profiles](https://www.jetbrains.com/help/qodana/inspection-profiles.html)
- [Qodana Python linter](https://www.jetbrains.com/help/qodana/python.html)
- [Local environment preparation](evaluate-locally.md)
