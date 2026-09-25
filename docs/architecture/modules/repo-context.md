# Repository Context Module Specification

Status: module specification

Date: 2026-09-25

## 1. Owned Invariant

Planning input is complete, fresh, canonical, and provenance-bound, or it is
explicitly marked as fallback-invalidating.

Every admitted graph is bound to the exact repository epoch, diff hash, config
epoch ID, dynamic policy hash, and compiled-policy hash observed at graph
admission. `build_planning_input` compares those coordinates before a planner
can consume the context. A graph from a different diff, epoch, or policy is
FullCI-invalidating even when its own local freshness predicate was once true.
The graph hash seals its payload, provenance, invalidating reasons, and all
admitted coordinates. Replacing one sidecar coordinate while retaining the
previous graph hash is a typed construction failure, rather than an input that
can reach a reduced plan.

Before any GitHub endpoint addressed by owner and repository name is called,
the provider resolves `/repositories/{repository_id}`. The response must bind
the expected repository ID, owner, and name to one internally consistent
canonical `full_name`; all later diff, metadata, and contents requests use that
resolved repository value. A rename observed only by the provider, including a
reuse of the old owner/name by another repository, is FullCI-invalidating until
the trusted context carries the new canonical owner/name.

## 2. Public API

```text
build_diff_context(repo_epoch) -> DiffContext
load_dependency_graph(repo_epoch, diff, policy) -> DependencyGraphContext
PolicySnapshot.from_projection(dynamic_ci_projection) -> PolicySnapshot
build_planning_input(repo_epoch, diff, graph, policy) -> PlanningInput
matches_path_pattern(pattern, path) -> bool
parse_workflow_capability(content, path, revision_sha) -> RevisionWorkflowCapability | absent
ProviderWorkflowInventory.admits_static_job(workflow_path, job_id) -> bool
ProviderWorkflowInventory.admits_static_gate(workflow_path, job_id, job_name) -> bool
ProviderWorkflowInventory.admits_exact_job_topology(workflow_path, expected) -> bool
ProviderWorkflowInventory.admits_local_reusable_workflow_closure(...) -> bool
ProviderWorkflowInventory.admits_control_plane(...) -> bool
```

Workflow capability YAML admission limits both composition passes to depth 64,
counting the root as depth one, before recursively composing deeper nodes. The
post-composition graph walk retains its independent node, depth, scalar, tag and
alias checks. Resource exceptions (`RecursionError`, `MemoryError`) yield absent
capability on a best-effort basis; this is not an RSS limit or guaranteed recovery
from process memory exhaustion. Existing byte bounds and scalar resolution remain
unchanged.

Static runner selectors accept a label scalar or nonempty label sequence, also
inside the mapping form with optional `group`. A group and all labels are
conjunctive. Labels retain bounded ASCII, case normalization, uniqueness and
cardinality checks; group text retains its exact case. An invalid, dynamic or
unsupported selector remains unknown, never a partially admitted label subset.
Selector evidence alone grants no execution authority.

## 3. Inputs

- repository identity and event identity.
- base SHA and head SHA.
- GitHub diff data.
- repository-owned dependency graph artifact.
- active policy epoch.

Dependency-graph admission separately enforces conjunctive limits of 1,000,000
decoded artifact bytes and 100,000 exact path nodes. In schema v1 the byte cap
also bounds node cardinality; the explicit node cap remains a versioned
defense-in-depth guard rather than an independent capacity claim. The byte cap
stays within the GitHub Contents API's fully supported file range for the
pinned API version; larger files require a different media-type protocol. The
provider adapter separately admits at most 2 MiB for the Contents JSON envelope
so base64 encoding does not silently narrow the content contract. Other
repository and diff JSON responses retain their narrower 1 MiB limit. A target
whose complete graph exceeds either graph limit is not partially admitted: the
graph is unavailable and planning selects FullCI. See the
[GitHub Contents API](https://docs.github.com/en/enterprise-cloud@latest/rest/repos/contents?apiVersion=2026-03-10#get-repository-content).

`PolicySnapshot` retains the policy-owned dependency-graph source and global
risk paths. A graph artifact must declare the same values. A mismatch is
FullCI-invalidating, and the planner consumes the policy-owned global-risk
paths rather than accepting a weaker artifact-side replacement.

The GitHub adapter cannot take a candidate's graph as its own trust authority.
It rejects a diff changing `.ci-coordinator/dependency-graph.v1.json` (including
rename/deletion evidence), and otherwise requires the exact artifact bytes at
the requested base and head to be equal. An absent, malformed, unavailable or
different baseline cannot authorize omission. This guard is coordinator-owned,
not a removable `invalidatesWhenChanged` entry in the artifact. The admitted
artifact remains bound to the current head; self-CI inventory checks still
apply in addition to baseline equality.

Equality establishes non-replacement by this candidate, not approval of the
baseline, completeness of its dependency edges, or protected-branch governance.
Those remain independent input-closure and production-admission obligations.
Changing or first introducing a graph therefore requires FullCI for that
transition; an unchanged graph retains selective eligibility under the other
existing predicates. Each Contents read retains its own byte bounds and the
outer acquisition deadline; no unbounded history scan or new cache is added.

The pure path language and matcher are owned by `kernel.path_patterns`.
`repo_context.freshness` retains explicit re-exports of its four public
functions for existing consumers. Repository/graph freshness and fallback
decisions remain here; moving syntax does not reinterpret persisted paths.

Path-pattern admission is bounded to 512 pattern characters and 4,096 path
characters. Matching uses explicit reachable path offsets rather than a regex
engine. For pattern length `P` and path length `S`, tokenization is `O(P)`, each
literal character participates in at most `S` prefix comparisons, and wildcard
closures scan at most `S + 1` offsets per token. The resulting worst-case time
is `O(P * S)` and retained matcher state is `O(P + S)`.

## 4. Failure Behavior

```text
TruncatedDiff => FullCI-invalidating context
PullRequestEpochMismatch => FullCI-invalidating context
MutablePullRequestFilesDifferFromImmutableCompare => FullCI-invalidating context
UnboundOrInconsistentComparisonMetadata => FullCI-invalidating context
NonPullRequestBehindOrDivergedComparison => FullCI-invalidating context
RepositoryIdentityMismatch => FullCI-invalidating context before owner/name lookup
UnknownFileStatus => FullCI-invalidating context
UnsafePath => FullCI-invalidating context
UnsafePatch => FullCI-invalidating context without a patch hash
IncompleteRenameEvidence => FullCI-invalidating context
StaleGraph => FullCI-invalidating context
UnknownWorkflowInventory => no target execution authority
UnboundStaticGateName => no target execution authority
StaticJobDependencyMismatch => no target execution authority
```

An immutable comparison binds the requested base, merge base, comparison
status/counts and the most recent returned commit to the requested head. A
pull request retains the provider's three-dot semantics, including a consistent
diverged or behind relation. A `push` or `merge_group` requires `ahead` with
the requested base as merge base, or `identical` with equal base/head and empty
files. Behind/diverged branch transitions cannot use a three-dot file list as
their complete before/after diff. The `forced` webhook flag alone is neither
required evidence nor sufficient proof of an unsafe tree transition.

The adapter uses the documented unpaged comparison (at most 250 commits,
whose final commit is the most recent comparison commit); it does not invent
a `head_commit` response field. The existing 300-file boundary and incomplete
pagination fallback remain. See the provider's
[comparison contract](https://docs.github.com/en/rest/commits/commits#compare-two-commits).

## 5. Proof Obligations

| Obligation                                               | Falsifier                                                                                                       |
|----------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| Diff ordering is canonical.                              | Reordered files change `diffHash`.                                                                              |
| Source metadata is hash-bound.                           | Same files with incomplete pagination keep same hash.                                                           |
| GitHub reads retain immutable repository identity.       | Repository A is renamed and its old owner/name is reused by B, then B's diff is admitted for A.                 |
| Stale graph never enables omission.                      | Old graph omits affected check.                                                                                 |
| Context cannot be replayed for another diff.             | Graph admitted for diff A accepts invalidating diff B.                                                          |
| Contents transport does not narrow graph admission.      | A valid near-limit graph is rejected only because its base64 JSON envelope exceeds the ordinary response limit. |
| Admitted path patterns have a polynomial resource bound. | Repeated wildcard/literal prefixes invoke a regex engine or require more than `O(P * S)` transitions.           |

## 6. Implementation Mapping

Target files:

```text
ci_coordinator/repo_context/diff_model.py
ci_coordinator/repo_context/diff_builder.py
ci_coordinator/repo_context/dependency_graph.py
ci_coordinator/repo_context/freshness.py
ci_coordinator/repo_context/planning_input.py
ci_coordinator/repo_context/workflow_inventory.py
ci_coordinator/repo_context/workflow_syntax.py
ci_coordinator/integrations/github/repository_context*.py
ci_coordinator/integrations/github/workflow_inventory.py
```

## 7. Acceptance Tests

- diff truncation and pagination fixtures.
- repository ID, stale owner/name reuse, canonical rename, and cancellation fixtures.
- unsafe path and rename evidence tests.
- unsafe patch test proving malformed Unicode cannot escape canonical hashing.
- graph freshness and dangling edge tests.
- near-limit graph, oversized graph, and independent Contents-envelope tests.
- canonical input hash golden fixtures.
- hostile YAML, invalid revision, incomplete pagination, inactive workflow, and
  missing exact-revision static-job tests;
- explicit static gate id/name, dynamic name, matrix, reusable-call,
  static-dependency closure/drift, and duplicate-name falsifiers.

`load_dependency_graph` receives the admitted diff, or evidence that binds the
same exact diff identity. A graph freshness decision that cannot observe the
changed paths cannot prove that an invalidator remained untouched; it must
return a FullCI-invalidating context instead.

Repository-context witnesses prove only the deterministic construction and
freshness laws owned by this module. Production enforcement additionally
requires live provider, bootstrap, shadow, rollback, and stable-gate evidence;
none of those external facts can be inferred from a local planning-input test.
Provider workflow inventory is a separate execution-admission fact specified
by `execution-identity.md`; it is not repository evidence consumed by the
coverage planner.
