# Repository utility checks

Run `tooling/quality/.venv/bin/python -m scripts.ci_utility_checks CHECK_ID`
from the repository root after `uv sync --project tooling/quality --frozen`.
The checker never installs tools. `commands(root, check_id)` returns immutable
`UtilityCommand` records containing `argv`, `cwd`, and an output contract.
`run` applies that contract after the bounded subprocess succeeds. The shared
CI matrix owns snapshot binding and group execution receipts.

The inventory includes Git-tracked and nonignored untracked files across every
root. Selected sources must be regular UTF-8 files without symlink components.
Empty selections, unsupported paths, nontext inputs, and exceeded bounds fail;
limits never truncate the selection into a passing subset. Bounds are 50,000
paths, 4,096 UTF-8 bytes per path, 16 MiB per file, 256 MiB of selected source,
16 MiB subprocess output and 600 seconds per command. Ignored caches, private
files and dependency installations are outside this inventory. The inventory
is a worktree observation, not an atomic filesystem snapshot.

| Check ID       | Tool and exact policy                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
|----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `yaml`         | [yamllint 1.38.0](https://yamllint.readthedocs.io/en/stable/configuration.html), every `.yaml` and `.yml`, explicit `yamllint.yaml`, strict failures. The configuration checks syntax, duplicate keys, anchors, punctuation and whitespace. YAML truthiness checks values; GitHub's `on` key remains valid. Presentation choices such as line width and document-start markers have no semantic gate.                                                                              |
| `schemas`      | [check-jsonschema 0.38.0](https://check-jsonschema.readthedocs.io/en/latest/usage.html), bundled `vendor.dependabot` and `vendor.compose-spec`, immutable official Dev Container core schema, and metaschema validation of every `*.schema.*.json`. Only builtin JSON Schema dialects are admitted.                                                                                                                                                                                |
| `hadolint`     | [Hadolint 2.15.1](https://github.com/hadolint/hadolint/tree/v2.15.1#configure), every `Dockerfile`, `Dockerfile.*` and `*.Dockerfile`. JSON preserves all diagnostics; inline ignore pragmas are disabled. The wrapper rejects every diagnostic except the five exact source-bound rows in `hadolint-exceptions.json`. Duplicate diagnostics and source drift fail.                                                                                                                |
| `build-checks` | [Buildx 0.37.1](https://github.com/docker/buildx/releases/tag/v0.37.1) with [BuildKit 0.33.0](https://github.com/moby/buildkit/releases/tag/v0.33.0), every Dockerfile, repository-root context, fixed `ci-utility-checks` builder. Every builder node must report the pinned version and running status. [`--check`](https://docs.docker.com/build/checks/) and `BUILDKIT_DOCKERFILE_CHECK=error=true` make violations fail without building an image. CI provisions the builder. |
| `gofmt`        | [Go 1.27.1 gofmt](https://pkg.go.dev/cmd/gofmt), every `.go` file including tests. Any `-l` output fails even when the tool returns zero.                                                                                                                                                                                                                                                                                                                                          |
| `go-vet`       | [Go 1.27.1 vet](https://pkg.go.dev/cmd/vet), `go vet -mod=readonly ./...` in every discovered module.                                                                                                                                                                                                                                                                                                                                                                              |
| `staticcheck`  | [Staticcheck 2026.2.1 / module 0.8.1](https://staticcheck.dev/docs/running-staticcheck/cli/), all checks with tests enabled in every Go module.                                                                                                                                                                                                                                                                                                                                    |
| `govulncheck`  | [govulncheck module 1.8.0](https://pkg.go.dev/golang.org/x/vuln@v1.8.0/cmd/govulncheck), all module packages and tests, text output and the official vulnerability database. JSON/SARIF are deliberately avoided because they return zero even for findings. This check needs network access.                                                                                                                                                                                      |
| `spelling`     | [codespell 2.4.3](https://github.com/codespell-project/codespell/tree/v2.4.3), all remaining regular text sources including historical documentation and hidden configuration. Uses its `clear,rare` dictionaries, explicit configuration and the shared [accepted vocabulary](spelling-words.dic). No source rewriting or file-wide spelling suppression.                                                                                                                         |

All utilities verify their exact versions. Go checks use Linux/amd64 with CGO
disabled, no automatic toolchain switching, no Go workspace, read-only module
mode, the official module proxy and checksum service. Every Go source must have
one nearest module owner. Build-tagged, platform-specific, hidden, vendor and
testdata Go sources require an explicit additional configuration owner and fail
admission; they are not silently omitted by `./...`. A new `go.work` or
`staticcheck.conf` likewise requires admission. The runtime environment passes
only PATH, HOME and TMPDIR plus fixed check variables; ambient tool options,
Python search paths, provider credentials and Go configuration are not forwarded.
The CI runner owns the tool installation and its HOME cache/configuration.

Before any of the four Go checks, execution runs the pinned
[`go list -e -find -json`](https://pkg.go.dev/cmd/go#hdr-List_packages_or_modules)
in every module. Its effective `GoFiles`, `TestGoFiles` and `XTestGoFiles` must
equal the complete Git-observed cohort for that module. Nonempty `CgoFiles`,
`IgnoredGoFiles`, `InvalidGoFiles`, package errors, duplicate records, missing
files and extra files fail before the analyzer runs. This includes implicit
cgo constraints from importing `C`, including grouped, raw and escaped import
syntax; source regexes do not decide cgo applicability. `-find` loads package
selection without resolving dependencies, compiling packages or running tests.
`-e` keeps errors in the JSON stream so the wrapper can reject them explicitly.
The pinned [Go 1.27.1 serializer](https://github.com/golang/go/blob/go1.27.1/src/cmd/go/internal/load/pkg.go)
omits zero-valued lists and error fields, including explicitly requested fields.
Those absent fields use their native zero values. An absolute `Dir` and exact
nonempty equality with the expected Git cohort remain mandatory; omission of
an effective source is therefore a failure, not an empty-input success.
Pure planning and self-CI generation declare this obligation without executing
Go or claiming native source-selection proof. The separate GitHub-only
`python -m scripts.conformance.go_utility_input_witness` proves a plain positive
case and four cgo counterexamples against the pinned native parser with network
access disabled; temporary fixtures are removed at the end.

Hadolint runs with `--no-fail` only so the wrapper can inspect the complete JSON
diagnostic set before deciding its own exit status. The pinned native tool
emits malformed Dockerfile input as `DL1000`; the wrapper rejects that error and
every warning outside the exact exception set even though Hadolint itself
returns zero. `failure-threshold: none` is not used as a synonym for this mode.

## Exact exclusions and predicate boundaries

The native Compose owner validates
`docker/development/compose.debug.yaml`, including `!reset`, through
`scripts/dev_environment/debug_witness.py` and the Compose adapter's `config`
operation. That exact file is excluded from check-jsonschema because its YAML
parser does not understand the Compose tag; another similarly named file is
still selected. YAML lint continues to include the override. The base Compose
schema does not replace merged-model, interpolation or runtime validation.

`schemas/devcontainers/provenance.json` binds the unmodified official Dev
Container core schema to commit and SHA-256. All its `$ref` values are local
fragments. VS Code runtime settings and extension-specific customizations are
outside this schema predicate; the native developer-environment witness retains
its existing authority. GitHub workflow semantics remain owned by native
actionlint and repository checks; this utility does not duplicate them with an
older generic workflow schema.

Spelling skips known binary asset suffixes, exact dependency lock paths, and
immutable upstream schema text. The SPDX schema exclusion checks the SHA-256
owned by `docs/specs/ci-coordinator-release/overview.md`; source drift fails.
Dev Container schema bytes are bound by their provenance. Generated spelling
exclusion files are checked against their exact owning source paths:

| Source                                                | Reason for exact line exclusions                                               |
|-------------------------------------------------------|--------------------------------------------------------------------------------|
| `backend/tests/unit/config_control/test_semantics.py` | Deliberately invalid Unicode-escaped identifiers in negative fixtures.         |
| `scripts/dev_environment/log_transport.py`            | The standardized HTTP transfer coding request header.                          |
| `scripts/dev_environment/watch_witness.py`            | A deliberately shared prefix matching singular and plural Compose diagnostics. |
| `scripts/tests/test_dev_environment_log_transport.py` | UTF-8 wire and Unicode escape fixtures.                                        |
| `scripts/tests/test_documentation_graph.py`           | A percent-encoded non-ASCII fragment rejection fixture.                        |

Each exception must occur exactly once in its owner and is passed only to that
file's codespell invocation. A changed or duplicated exception fails admission;
nearby lines and every other file remain checked. Source or tool changes that
invalidate an exception require fresh review. The alternative of rewriting
upstream schemas or intentional failure fixtures would change protected input
contracts without improving spelling coverage.
