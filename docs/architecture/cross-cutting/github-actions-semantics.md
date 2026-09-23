# GitHub Actions Semantics Specification

Status: cross-cutting specification

Date: 2026-07-30

## 1. Owned Invariant

GitHub Actions behavior is external platform physics. CI Coordinator may build
policy on top of it, but must not assume behavior GitHub does not provide.

## 2. Platform Laws

| Id    | Law                                                                                                                                                                                                                                                                           | Consequence                                                                                                                                                                                                                                                      |
|-------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| GH-1  | A path-filtered required workflow can remain pending.                                                                                                                                                                                                                         | A workflow owning the stable required gate must not use `paths` or `paths-ignore`.                                                                                                                                                                               |
| GH-2  | A condition-skipped job can report success.                                                                                                                                                                                                                                   | GitHub success is not semantic success.                                                                                                                                                                                                                          |
| GH-3  | Dynamic matrix is possible inside static workflow structure.                                                                                                                                                                                                                  | Matrix shape may be dynamic; required gate identity must remain stable.                                                                                                                                                                                          |
| GH-4  | `workflow_dispatch` runs only when the workflow file exists on the default branch, while the dispatched ref can be a branch or tag.                                                                                                                                           | Dispatch mode uses a provider-observed workflow inventory, not runtime workflow generation.                                                                                                                                                                      |
| GH-5  | Merge queue requires `merge_group` support.                                                                                                                                                                                                                                   | Enforcement is blocked until the gate-owning workflow handles merge groups.                                                                                                                                                                                      |
| GH-6  | Runner status is a snapshot, not a reservation.                                                                                                                                                                                                                               | Capacity optimizes shard count, not coverage.                                                                                                                                                                                                                    |
| GH-7  | Check source identity can matter for required checks.                                                                                                                                                                                                                         | Required gates must model expected source app where configured.                                                                                                                                                                                                  |
| GH-8  | Branch protection requires a check-run identity, not merely a workflow display name.                                                                                                                                                                                          | One always-run aggregator job owns the stable required-check identity.                                                                                                                                                                                           |
| GH-9  | An unavailable context property evaluates to an empty string, which is not valid JSON input for `fromJSON`.                                                                                                                                                                   | Every JSON-valued plan output used in a job condition must have an in-expression valid-JSON default.                                                                                                                                                             |
| GH-10 | GitHub expression string comparisons and `contains()` are case-insensitive.                                                                                                                                                                                                   | Every job ID used in selection expressions must be unique under ASCII case folding.                                                                                                                                                                              |
| GH-11 | On GitHub.com, same-repository reusable workflows use `$/.github/workflows/{file}` or `./.github/workflows/{file}` and resolve from the caller commit; the dollar-root form is unavailable on GitHub Enterprise Server.                                                       | GitHub.com admission canonicalizes both documented job-level forms to one repository-relative path; action-step grammars and GHES non-support cannot broaden authority.                                                                                          |
| GH-12 | A called reusable-workflow tree is limited to ten connected levels and fifty unique called workflows.                                                                                                                                                                         | Static closure must fail before either provider ceiling; a stricter product bound is valid.                                                                                                                                                                      |
| GH-13 | On GitHub.com, `$/path/to/action` resolves in the workflow repository at the running commit without checkout, while `./path/to/action` resolves in the checked-out workspace.                                                                                                 | Admission accepts both static forms with canonical bounded paths. The product grammar admits no `@` character in a `$` path, so no suffix can be interpreted as a separate ref; admission does not infer source or behavioral equivalence between the two forms. |
| GH-14 | A reusable workflow OIDC token describes the caller in standard workflow claims and the called workflow in `job_workflow_ref` and `job_workflow_sha`.                                                                                                                         | Selected execution requires both namespaces and binds the called workflow to the target registry.                                                                                                                                                                |
| GH-15 | A remote reusable workflow may fail graph resolution before any called or dependent job executes.                                                                                                                                                                             | Post-resolution failures can select local FullCI; access or reference resolution failures are hard provider failures and require an independent native path during adoption.                                                                                     |
| GH-16 | Job outputs have an aggregate provider budget and values passed through process environments have a smaller per-value operating-system bound.                                                                                                                                 | Signed plan transport uses bounded canonical chunks and rejects the complete transport before allocation or selected execution.                                                                                                                                  |
| GH-17 | In a reusable workflow, `github.workflow_ref` and `github.workflow_sha` retain caller identity, while `job.workflow_ref` and `job.workflow_sha` identify the workflow file that defines the current job; the job identity fields are unavailable on GitHub Enterprise Server. | A credential-free job compares caller and defining refs exactly before request activation; plan and gate authority remains bound to defining-job identity. The mixed-invocation profile is GitHub.com-only.                                                      |
| GH-18 | GitHub expression string comparisons and `startsWith` are case-insensitive, and the `job` context is unavailable in `jobs.<job_id>.if`.                                                                                                                                       | A job-level expression cannot prove exact caller/defining identity; the fixed classifier performs a case-sensitive comparison over environment data and exports only a projected boolean.                                                                        |

## 3. Required Gate Model

```text
RequiredGate =
  name
  expected_source_app_id?
  expected_sha_binding
  workflow_name
  aggregator_job_id
  allowed_github_conclusions
  semantic_success_predicate
```

Rule:

```text
RequiredGatePass =
  GitHubReportedExpectedSource
  and SemanticGreen(plan)
```

## 4. Adopted Workflow Requirements

- the target profile declares a non-empty subset of `pull_request`, `push`, and
  `merge_group`; it includes every event on which its required gate is expected
  and includes `merge_group` whenever merge queue is enabled;
- manual dispatch is optional and always uses FullCI unless the target profile
  separately admits selected execution for that mode;
- one fixed credential-free classifier runs before the requester and emits
  `direct=true` only when non-empty `github.workflow_ref` and
  `job.workflow_ref` are exactly equal under a case-sensitive shell comparison;
- the plan requester depends on that classifier and runs only when its exact
  output is true and `github.event_name` belongs to the declared dynamic-event
  subset; a target may additionally exclude draft pull requests through the
  one admitted exact condition;
- same-repository reusable invocation skips the plan requester and reaches the
  existing native FullCI route; plan and gate jobs use `job.workflow_ref` and
  `job.workflow_sha` so caller identity cannot replace defining-workflow
  authority;
- no workflow-level or trigger-level path filters.
- no `pull_request_target`.
- one full-SHA platform reusable-workflow job requests `id-token: write` and
  executes no target-controlled bytes;
- one target plan job has no OIDC permission, depends exactly on the request
  job, and runs after every non-cancelled request outcome;
- execution jobs use the minimum required permissions.
- coordinator timeout maps to FullCI.
- invalid, missing, mismatched, or expired signed plan maps to FullCI.
- aggregator job uses always-run semantics.
- aggregator succeeds only after exactly one successful SelectedChecks or FullCI
  execution path.
- classifier, request, plan, and gate jobs match one generated normalized safety projection, every
  reachable workflow omits workflow-level environment/defaults, and no job or
  step in that closure declares `continue-on-error`.
- same-repository actions use a static canonical `$/` or `./` path; `$` is
  preferred when the target supports GitHub.com-only execution because it is
  bound to the running workflow commit and does not require checkout.
- JSON-valued plan outputs used by job conditions default to a valid closed
  value such as `[]` before `fromJSON`, including when the plan job never
  publishes outputs.
- classifier, request, plan, execution, fallback, and gate job IDs are pairwise
  unique under ASCII case folding before any selected plan can be issued.
- required gate name is stable.
- every third-party action reference is an immutable commit SHA.

The workflow may be an existing repository-native workflow or a generated
witness-shard workflow. Its commands, runners, permissions, secrets, services,
environments, dependencies, and aggregate gate remain repository-owned.

## 5. Dynamic Matrix Requirements

```text
MatrixPlan is valid iff:
  JSON schema is admitted
  matrix size is within policy limits
  every selected check has exactly one execution entry unless replication is explicit
  empty matrix has omission proof for every check
  stable required gate is independent of matrix cardinality
```

## 6. Dispatch Requirements

```text
CanDispatch(workflow) =
  ExistsOnDefaultBranch(workflow)
  and SupportsWorkflowDispatch(workflow)
  and InputsFitGitHubLimits(workflow)
  and RefIsExplicit(workflow)
  and PermissionsAllowDispatch(workflow)
```

Dispatch mode is not the first production enforcement mode because coordinator
unavailability would sit on the critical path for starting CI.

## 7. Sources

- [Troubleshooting required status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks)
- [Using conditions to control job execution](https://docs.github.com/actions/using-jobs/using-conditions-to-control-job-execution)
- [Running variations of jobs in a workflow](https://docs.github.com/actions/writing-workflows/choosing-what-your-workflow-does/running-variations-of-jobs-in-a-workflow)
- [REST API endpoints for workflows](https://docs.github.com/en/rest/actions/workflows)
- [Managing a merge queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)
- [REST API endpoints for self-hosted runners](https://docs.github.com/en/rest/actions/self-hosted-runners)
- [Contexts reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)
- [Evaluate expressions in workflows and actions](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions)
- [Workflow syntax for GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [Reuse workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
- [Reusable workflow limits](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)
