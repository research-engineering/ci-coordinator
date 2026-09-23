# Periodic Advisory Checks

Owner: repository CI and `ci-coordinator.release`.

`Scheduled Advisory Check` queries current advisory services every day at
03:23 UTC and supports manual dispatch from `master`. It runs independently of
Full Check and creates no dependency pull requests, issues, releases or deployments.
The existing release requirement `REQ-CI-RELEASE-001` and dependency/tooling
requirement `REQ-CI-PROOFKIT-012` retain their authority. This workflow adds an
observation schedule and bounded receipts; it does not change their thresholds.

## Subjects and existing owners

| Subject        | Native owner                                                         | Coverage                                                                                               |
|----------------|----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| Python         | `scripts/dependency_audit.py`, `uv audit --frozen`                   | Both backend and quality locks, every profile-supported Python runtime on Linux, all dependency groups |
| npm            | `scripts/dependency_audit.py`, `pnpm audit --audit-level high`       | Complete workspace lock, including development dependencies                                            |
| Go             | `scripts/ci_utility_checks.py`, `govulncheck`                        | Every declared module and its admitted effective Go sources, tests included, Linux amd64               |
| Released image | `scripts/release_vulnerability_admission.py` and its existing policy | Latest successful Release Artifact publication, exact OCI index and its unique Linux amd64 child       |

Package commands come from their existing owners. Native nonzero exits,
transport failures, process lifecycle failures and missing inputs fail the
observation. Python lock observations continue independently after a finding;
the Python, npm, Go and image jobs do not cancel one another. Go retains the
native text result because its JSON and SARIF formats return success even when
vulnerabilities are found. No ignore, fix-only, automatic repair or severity
waiver is introduced.

Package receipt admission independently reconstructs the ordered native argv
and repository-relative working-directory plan from these owners and compares
every input hash with the checkout. A version command or an incomplete audit
cannot stand in for the complete plan. The pnpm reader validates both documents
of the pinned native lock stream: optional bootstrap metadata followed by the
nonempty project graph. Invalid intake still retains a failed receipt.

## Immutable release discovery

The first bounded page of successful `Release Artifact` runs on `master` is
required to be nonempty, complete for its 100-run page and newest first. The
newest created release run is selected; ambiguous timestamps fail. Its
`run-<id>-<attempt>` tag is only a discovery address. All later operations use
the resolved immutable digest. Missing tags, attestations, history or platform
evidence fail the image job; they never remove it from required coverage.

GitHub and OCI attestation verification both require the exact repository,
source SHA, `master`, release workflow and hosted runner. The verified
certificate's invocation URI and immutable repository identifier bind the
digest to the selected release run and attempt. This uses certificate fields
derived from GitHub's OIDC identity, rather than trusting a mutable tag or the
workflow-controlled predicate alone. The canonical packaged build identity is
copied from an unstarted container and must be production eligible with the
same source SHA. The image is never executed by the scan.

The existing Grype gate then binds the exact index, platform child, scanner
release checksum and fresh database. This observes one latest published image;
it does not enumerate deployment targets or prove that every historical image
still running somewhere was checked. Additional deployed subjects require an
explicit owned inventory. Release discovery does not depend on expiring build
artifacts and does not reapply today's Full Check event policy to old releases.
The coverage reader separately reads the bounded native Grype admission receipt,
checks its checksum, accepted owner policy and exact image/platform/index
correlation. This is readback consistency for a trusted successful producer,
not a second cryptographic verification or a replacement scanner policy.

## Freshness and notification boundary

Receipts contain source and lock hashes, native command/result hashes, bounded
advisory identifiers and UTC observation time. Raw provider responses, process
logs, environment and tokens are not uploaded. Receipts are retained for seven
days. The coverage job requires all four same-source successful receipts and
rejects future observations or observations older than 36 hours. Reusing a
receipt later requires this same freshness predicate; a past green status is
not a permanent clean bill of health.
It also requires both prerequisite jobs to finish successfully, including
artifact upload and action cleanup; retained files alone cannot override a
failed prerequisite job.

Python disables its advisory cache and queries OSV; npm queries its registry;
Go queries `vuln.go.dev` in a fresh job. These online services do not expose one
common authoritative feed-build timestamp in the consumed native output.
Their receipt records observation time and response hashes, and explicitly
leaves the global timestamp unavailable. Go additionally retains the database
timestamp emitted by its version command when available; that preliminary
metadata does not prove the exact snapshot consumed by a later scan.
Grype separately enforces its
existing maximum database age of 24 hours and retains its database timestamp
and checksum. A new advisory can invalidate an earlier observation even when
the source and locks have not changed.

Use GitHub Actions email/web notifications with failed-workflow notifications
enabled. GitHub associates scheduled-run notifications with the user who last
changed the cron expression. This repository does not claim to configure a
person's subscription or to have delivered a notification. It creates no issue
and sends no custom message.

GitHub may delay or drop scheduled jobs, disable inactive public-repository
schedules or be unavailable. With no run there is no new receipt or native
failure notification; coverage becomes unknown after 36 hours. This workflow
is not an independent availability monitor. Before relying on old evidence,
check the latest receipt and dispatch a fresh observation if necessary.

## Maintenance and qualification

Keep scanner/runtime/action pins aligned with their existing owners. The
new certificate JSON consumer pins GitHub CLI 2.101.0 and its Linux release
checksum instead of inheriting the runner's CLI version. The
workflow's fixture checks cover wrong runs, attempts, repositories, source
digests, retagged images, missing evidence, lifecycle failures and freshness.
GitHub native checks qualify the actual package commands. A fixture pass does
not prove an actual image scan or active scheduling before this workflow is
merged onto the default branch. No full daily behavioral test suite is needed
to ask the advisory services whether the existing subjects acquired findings.

Revise the design if GitHub exposes a simpler authenticated immutable release
subject, if a feed timestamp becomes available, or if the user requires an
independent missed-run alert or an inventory of deployed historical images.

Primary contracts: [uv audit](https://docs.astral.sh/uv/reference/cli/#uv-audit),
[pnpm audit](https://pnpm.io/cli/audit),
[govulncheck](https://pkg.go.dev/golang.org/x/vuln/cmd/govulncheck),
[GitHub attestation verification](https://cli.github.com/manual/gh_attestation_verify),
[Sigstore certificate extensions](https://github.com/sigstore/sigstore-go/blob/v1.3.0/pkg/fulcio/certificate/extensions.go),
[flat certificate serialization](https://github.com/sigstore/sigstore-go/blob/v1.3.0/pkg/fulcio/certificate/summarize.go),
[pnpm lock document stream](https://github.com/pnpm/pnpm/blob/v12.5.1/pnpm/crates/lockfile/src/yaml_documents.rs),
[scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
and [Actions notifications](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs).
