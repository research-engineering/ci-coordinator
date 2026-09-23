# Supported Runtime Lines And Dependency Consolidation

Status: accepted for the dependency update following the comprehensive CI matrix.
Owner: runtime and developer-environment contracts.

## Problem And Scope

The comprehensive CI matrix selected Python 3.14 and Node 26 Current while
updating the complete toolchain. The required support boundary is now Python
3.13.15 and Node 24.21.0 LTS. Independently applying runtime, typings and
container proposals can violate the single-patch runtime contract or restore
older dependency versions already superseded by the matrix update.

## Decision

Admit Python 3.13.15 and Node 24.21.0 exactly in native manifests, lock files,
images, runtime profiles, workflow inputs and consumer runtime guards. Python
linting, type checking and formatting target 3.13. Node declarations use the
latest compatible 24.x typings, currently 24.13.6. Keep every other latest
compatible stable dependency selected as of 20 September 2026.

This decision supersedes only the runtime-line selection in the
[comprehensive CI matrix decision](comprehensive-ci-matrix.md). The matrix,
command grouping, trust boundaries and existing compatibility exceptions
remain owned by that decision and their native manifests. The
[toolchain update procedure](../how-to/toolchain-updates.md) owns atomic
updates and GitHub qualification; the canonical
[Python runtime profile](../specs/ci-coordinator-runtime/python-runtime-profile.v1.json)
owns the admitted runtime data.

Consolidate outstanding dependency proposals against the matrix branch.
Preserve each original proposal's commit ancestry on a separate integration
branch, and publish the reviewed final tree as one maintainer-authored commit
on the delivery branch. Reachability into the integration base records
original proposals as merged without introducing their author identities into
the delivery branch's new commit history. Original proposal metadata and the
integration branch retain attribution; author fields are not rewritten.

## Alternatives And Consequences

Accepting every proposed major is cheaper initially but violates the selected
support boundary. Squashing original proposal heads directly loses their
ancestry and cannot establish their indirect merge. Keeping all merge commits
in the delivery branch preserves ancestry but does not meet the requested
single-commit delivery shape. A separate integration branch and one fresh
commit satisfy both constraints at the cost of retaining an additional ref.

Version declarations and registry metadata establish candidate compatibility,
not runtime correctness. Regenerate native locks in GitHub, retain independently
resolved image digests, and qualify the complete locked runtime with existing
native checks. Preserve frontend type/API compatibility overrides until their
owners admit replacements. Python 3.14-only syntax is not admitted on 3.13.

Revisit this choice when the required runtime support line changes, a security
fix cannot be consumed within it, or exact-runtime qualification disproves a
compatibility assumption. A future update must change the complete coupled
runtime cohort; automated major proposals cannot change support policy alone.
