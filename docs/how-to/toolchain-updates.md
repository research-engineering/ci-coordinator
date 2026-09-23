# Update The Toolchain Atomically

Status: Dependabot retained; Renovate provider qualification is unmet. No Renovate
pilot, installation or lock-execution capability is claimed. This is the P7/J11
maintainer path from the [design](../features/developer-experience-convergence.md)
and [implementation plan](../features/developer-experience-convergence-implementation-plan.md).

## Check The Native Declarations

After the locked backend environment is installed, run from the repository root:

```sh
mise run toolchain:check
```

For explicit output formatting, the equivalent checker entry point is:

```sh
backend/.venv/bin/python -B -m scripts.dev_environment.toolchain --format json
backend/.venv/bin/python -B -m scripts.dev_environment.toolchain --format human
```

The default format is JSON. Exit `0` means the checked declarations agree; `1`
means drift, a missing/malformed owner, unsupported syntax or an input changed
during reading; argparse uses `2` for invalid arguments. The command reads the
worktree containing the module, without installing tools, creating instance
state, executing shell commands or contacting providers. It uses standard-library
TOML/JSON parsers and the backend's existing `ruamel.yaml` safe parser.

For a specific checkout, use `check_toolchain(repo_root: Path) -> ToolchainReport`
or `run(argv: Sequence[str], *, repo_root: Path, stdout: TextIO) -> int` from
`scripts.dev_environment.toolchain`. `run` accepts only `--format human|json`.
The report's `projection()` has schema `ci-coordinator-toolchain-check/v1`,
`state`, `tools`, source SHA-256 `inputs`, `issues` and observed `images`.
Each issue identifies `path`, `selector`, `reason` and `detail`. Checked files
are rehashed before returning; rerun after concurrent edits settle. There is no
cached workflow snapshot.

| Native owner                                                   | Relationship checked                                                                                               |
|----------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| `mise.toml`, `mise.lock`                                       | Literal Python/Node/uv/pnpm patch versions; one matching locked version and selector per tool                      |
| Root and frontend `package.json`                               | Node/pnpm engines match mise; root packageManager and any frontend override match pnpm                             |
| `backend/pyproject.toml`, `backend/uv.lock`                    | Exact Python requirement matches mise; mypy/Ruff target the corresponding minor line                               |
| Canonical and packaged `python-runtime-profile.v1.json`        | Current single admitted patch, requirement, container version and static minor target match                        |
| Root, backend-development and frontend-development Dockerfiles | Every recognized Python/Node/uv image and Corepack pnpm preparation agrees; required declarations cannot disappear |
| `.devcontainer/Dockerfile`, `devcontainer.json`                | MISE_VERSION and any build override equal the repository's mise minimum; the checked Dockerfile remains selected   |
| `.github/workflows/*.yml` and `*.yaml`                         | Direct setup-python/setup-node/setup-uv/pnpm action inputs match, including newly added jobs/files                 |

Docker inspection admits literal `FROM` and external `COPY --from=` references,
internal stage inheritance, optional FROM platform flags and backslash
continuations. It requires explicit tag plus SHA-256 digest on external images.
Variable image references and heredocs require an owner-reviewed extension;
they are not silently evaluated. Dev Container features introducing a second
toolchain also require review. This is a bounded declaration check, not a Docker,
shell or Actions interpreter. Arbitrary RUN scripts, composite/reusable workflow
internals and alternative setup actions remain with their native owners.

Unrelated action releases, Node typings, lint images and distro versions need not
equal tool versions. A future multi-runtime Python profile needs a compatibility
rule replacing the current single-patch relationship. Native lock format,
platform coverage, artifact checksums, runtime profile schema/byte parity and
Dev Container provisioning remain covered by their existing owner checks.
The existing Python `lock-check` gate owns coherence between `backend/uv.lock`
and its generated `backend/requirements-dev.lock` projection; the toolchain
checker does not duplicate that freshness check.

`digestIdentity: not_verified` remains explicit even on a passing report. An
equal version tag, a correctly shaped digest and even equal digest strings do
not prove registry provenance, manifest platform coverage or the executable's
version. Resolve each intended tag at its publisher registry, retain the returned
manifest/index digest and platform evidence, then build/pull that exact reference
in GitHub and inspect the runtime. Never carry an old digest onto a new tag just
to make the version checker pass. Docker documents why [digest pinning fixes the
selected artifact](https://docs.docker.com/build/building/best-practices/#pin-base-image-versions).

Ruff lint, formatting and mypy target the admitted Python 3.13 runtime.
Keep `ruff format --target-version py313` in the native formatting commands
and use the same task from editors. This also retains compatible syntax in
Python resources distributed to consumer repositories.

pnpm 12 reads installation policy from `pnpm-workspace.yaml`; `.npmrc` retains
its npm compatibility role. Keep `engineStrict: true` in the workspace owner.
The explicit `.pnpm-store` location preserves the Dev Container's existing
identity-scoped volume instead of relying on filesystem-dependent store
discovery. The native container witness checks the resolved and canonical path.
See the upstream [configuration](https://pnpm.io/settings) and
[store contract](https://pnpm.io/settings/store#storedir).

## Prepare One Coherent Update

1. Choose the intended tool versions and explain the compatibility/security reason
   in one proposal. Capture the base and inspect current Dependabot proposals.
   Preserve reviewed security fixes. Update only the affected relationships in
   the table; do not make unrelated components numerically equal.
2. Update native manifests, all affected image tags **and independently resolved
   digests**, runtime profiles and every corresponding CI setup input in that
   same proposal. For mise itself, also update Dev Container MISE_VERSION and
   its verified amd64/arm64 download checksums. Keep the debug dependency group
   and ordinary/release image separation intact.
   Update Compose qualification versions and release-asset checksums in the
   connected-stack matrix explicitly. Retain the admitted minimum and qualify
   the selected current release; a runner image's bundled version cannot stand
   in for either choice.
3. In the authorized GitHub maintenance environment, regenerate the applicable
   locks with the selected tools. The current native platform owner requires
   Linux arm64/x64 and macOS arm64/x64 for Node/Python/uv, and Linux arm64/x64 plus
   macOS arm64 for pnpm. Preserve that asymmetric set; adding a platform needs
   its own admission. For a coordinated update the explicit commands are:

   ```sh
   mise lock node python uv --platform linux-arm64,linux-x64,macos-arm64,macos-x64
   mise lock pnpm --platform linux-arm64,linux-x64,macos-arm64
   mise exec -- uv lock --project backend
   mise exec -- uv export --project backend --all-groups --format requirements.txt \
     --output-file backend/requirements-dev.lock --no-emit-project --frozen
   mise exec -- pnpm install --lockfile-only --ignore-scripts --no-frozen-lockfile
   ```

   `requirements-dev.lock` is the all-groups requirements projection of `uv.lock`,
   not an independent dependency resolution. Regenerate it in the same proposal
   after Python lock or dependency-group changes, including the debug group.
   Use the mise-selected uv and the relative paths and argument order shown above
   to preserve the canonical export header owned by
   [the Python environment witness](../../scripts/python_environment_witness.py).
   Review both locks together; a frozen export alone does not prove that `uv.lock`
   matches `pyproject.toml`.

   Use only the affected subset. Inspect every lock delta; do not hand-edit
   checksums or replace a lock wholesale to suppress conflicts. `mise lock`
   supports [explicit platform selection](https://mise.jdx.dev/cli/lock).
   The pnpm command can change manifests as well as its lock; inspect both
   according to the [install contract](https://pnpm.io/cli/install#--lockfile-only).
4. Run the static checker against the final candidate. In GitHub run the existing
   `python.lock-check` gate before frozen dependency installation:

   ```sh
   mise exec -- python3 -m scripts.python_witness lock-check
   ```

   This gate runs `uv lock --project backend --check`, admits the canonical
   requirements header, and compares a fresh frozen all-groups export against
   the retained projection. Require both lock freshness and projection coherence;
   toolchain version agreement cannot substitute for either. Then run the existing
   frozen dependency installs, Python runtime/profile witnesses, frontend and
   runtime image checks, and the Dev Container platform/provisioning owner.
   Include the new falsifiers in the existing scripts-test owner:

   ```sh
   backend/.venv/bin/python -m pytest -c backend/pyproject.toml scripts/tests/test_dev_environment_toolchain.py
   ```

   `test_current_native_declarations_pass_without_certifying_artifacts` checks the
   actual repository cohort. The mutation cases separately demonstrate that an
   inconsistent consumer, missing declaration or malformed source is rejected.

   Native tests/builds/browser/Docker witnesses run through GitHub, not locally.
   A passing static report is insufficient for lock regeneration or runtime
   qualification. Preserve existing security review and exact-revision required
   gates, including each added job in the aggregate gate's needs/results checks.
5. Merge the complete proposal only after the final revision's gates
   pass. Recheck the new base after conflicts; do not merge a tool-only or
   image-only fragment of an otherwise coupled update. On failure, repair the
   coherent set or withdraw the proposal while retaining security fixes. Do not
   downgrade dependencies merely to restore a prior bot configuration.

## Retain Security Alerts Without Automated Pull Requests

The owner-selected mode is advisory-only: retain **Dependabot alerts** in
GitHub's Security view, disable **Dependabot security updates**, and leave every
ecosystem's `open-pull-requests-limit` at `0`. The YAML limit disables version
update PRs; it does not disable security-update PRs. The latter is a separate
repository setting. Also inspect organization auto-triage rules: a rule that
opens a security PR is incompatible with this mode. Existing PRs are not closed
by this configuration and require separate triage.

GitHub security alerts represent known vulnerabilities, not every available
new version. An outdated but non-vulnerable dependency will not acquire a
Security entry merely because automated PRs are disabled. Container/runtime
vulnerability coverage is also not proved by enabling dependency alerts.
Keep the existing dependency-review and other security gates.

The September 2026 supported runtime selection is **Python 3.13.15** and
**Node 24.21.0 LTS**, coordinated across mise, manifests, images and locks.
Docker `python` ignores versions `>=3.14`; Docker `node` and npm `@types/node`
ignore versions `>=25` if version PRs are later re-enabled. Other dependencies
use the latest compatible stable release, with compatibility exceptions owned
by their native manifests. Node 24.21.0 includes Corepack 0.36.0; both Node
image recipes retain that exact Corepack pin before preparing pnpm.
The [runtime consolidation decision](../decisions/lts-dependency-consolidation.md)
owns this support-line selection and its revision conditions.

The procedure above owns manual coordinated updates; disabling proposals does
not make dependency freshness automatic. Review new advisories promptly and
check toolchain release/support status at least weekly. The retained schedules
and groups are dormant re-enablement configuration, not a delivery-time promise.
See [GitHub version-update controls](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/configure-version-updates)
and [security-update controls](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/configure-security-updates).

## Revisit Proposal Automation Only With Evidence

The unmet P7 criterion is a qualified proposal under actual installed-provider
permissions. Revisit only when an isolated Renovate pilot demonstrates extraction
of mise/uv/npm/Docker/Actions dependencies, regeneration of every admitted lock
platform, one complete Python update and one complete Node update, independent
tag/digest evidence and exact-revision required checks. The pilot must run without
additional runtime/release credentials. Record provider installation, permissions,
proposal refs and successful/failed criteria; a local configuration is not proof.

If admitted, transfer version-proposal ownership once, quiescing the old updater
for overlapping dependencies while retaining security alerts. On rollback,
quiesce the new updater first, explicitly resolve its open proposals, then restore
the old owner. Neither parallel bots nor a new self-hosted maintenance service is
an implicit fallback. Revisit the static checker separately when a native owner
changes its declaration syntax, adds a runtime or moves installation into an
uninspected action/script; add a causal falsifier with that change.
