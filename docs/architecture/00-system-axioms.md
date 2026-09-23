# System Premises And Platform Facts

Status: architecture premise authority

Last verified: 2026-09-02

## 1. Purpose

This document owns facts and owner decisions that cannot be derived from the
repository implementation. The term `premise` is deliberate: these statements
are not logical tautologies and do not make an architecture optimal by
themselves.

For architecture epoch `E`:

```text
PremiseKind(p) in {owner, external}

ValidPremise(p, E) :=
  (PremiseKind(p) = owner and OwnerAdopted(p, E))
  or
  (PremiseKind(p) = external and ExternallyVerified(p, E))

ValidPremise(p, E) -/-> PreferredArchitecture(d, E)
```

`PA-*` and `CA-*` are owner premises. `GA-*` and `TF-*` are external
facts. Owner adoption cannot make a false provider or technology fact valid,
and external observation cannot replace a product decision.

The [meta-specification](01-meta-specification.md) exclusively owns law
admission. This document supplies premise validity; it does not duplicate the
law predicate.

An architecture decision additionally needs an explicit context, alternatives,
hard constraints, evidence, falsifiers, and revision conditions. Those
derivations are owned by the [meta-specification](01-meta-specification.md),
[context map](02-context-map.md), and applicable decision records.

## 2. Product And Organization Premises

These premises are adopted by the repository owner. They remain valid only
while their revision trigger is false.

| Id   | Premise                                                                                                                                                                                         | Evidence or authority                                 | Revision trigger                                                                                                 |
|------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| PA-1 | Python is the sole backend production runtime through the first published release.                                                                                                              | Product owner and runtime profile                     | A supported non-Python backend runtime is required.                                                              |
| PA-2 | The backend team operationally owns Python and FastAPI.                                                                                                                                         | Team capability and operating model                   | Ownership moves or the team cannot operate the selected stack within its quality budgets.                        |
| PA-3 | The product includes a browser operator experience; TypeScript is permitted for its source, while server authority remains outside the browser.                                                 | Product owner and operator-UI requirement package     | The browser UI is removed or granted a separately admitted authority model.                                      |
| PA-4 | The product optimizes CI processor cost and latency only inside a validation-correct feasible set.                                                                                              | Product owner and accepted cost/safety scope          | An owner explicitly accepts weaker validation, which would require a replacement product law.                    |
| PA-5 | Operators require policy changes without process replacement, and accepted changes must remain durable, replayable, concurrency-safe, and rollbackable.                                         | Product and operations owners                         | Policy becomes deployment-only or any listed lifecycle property is no longer required.                           |
| PA-6 | Product support begins with the first published release. No earlier implementation or schema is a supported compatibility target.                                                               | Product owner                                         | A pre-release compatibility obligation is explicitly adopted.                                                    |
| PA-7 | Adopted target repositories remain the execution owner; the current control plane selects or shapes work but does not centrally execute repository commands.                                    | Product scope and target-adoption contract            | Central dispatch is separately admitted with outage, credential, and fallback evidence.                          |
| PA-8 | The current product has one backend deployment unit and one authoritative PostgreSQL store; no internal capability has an independent deployment or data-authority requirement.                 | Accepted backend architecture decision                | Independent ownership, scaling, availability, release, or data-authority evidence appears.                       |
| PA-9 | deployment-owned Keycloak owns human and workload administrator identity, one deployment-admitted GitHub App owns provider operations, and repository-owner attestation remains an independent authority. | Product owner and organization control-plane contract | The organization selects another identity provider, provider application owner, or repository-consent authority. |

## 3. GitHub Platform Facts

These are externally controlled facts. A source confirms only the stated
capability or behavior; it does not prove CI Coordinator's use of it correct.

| Id   | Verified fact                                                                                                                                                                                                       | Source                                                                                                                                                                                                                                                                  | Revision trigger                                                  |
|------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|
| GA-1 | A workflow skipped by branch, path, or commit-message filtering leaves its associated required checks pending.                                                                                                      | [GitHub required checks troubleshooting](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks)                                                     | GitHub changes required-check handling for skipped workflows.     |
| GA-2 | A job skipped by a job-level conditional reports success and does not block merging even when its check is required.                                                                                                | [GitHub job conditions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions)                                                                                                                              | GitHub changes skipped-job conclusions.                           |
| GA-3 | A static workflow can construct a job matrix from an earlier job's output through `fromJSON`.                                                                                                                       | [GitHub matrix documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations)                                                                                                                                   | GitHub removes or materially changes output-derived matrices.     |
| GA-4 | `workflow_dispatch` is received only when the workflow file exists on the default branch; the REST call requires an explicit branch-or-tag `ref`, the declared trigger, and at most 25 configured top-level inputs. | [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatch) and [dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)                                 | Default-branch, ref, trigger, or input semantics change.          |
| GA-5 | When a merge queue requires a GitHub Actions check, the reporting workflow must handle `merge_group`; otherwise the required check is not reported for the merge group.                                             | [GitHub required checks troubleshooting](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks#status-checks-with-github-actions-and-a-merge-queue) | Merge-queue required-check triggering changes.                    |
| GA-6 | The self-hosted-runner REST inventory exposes observed `status` and `busy` values; that observation is not a reservation or future-capacity guarantee.                                                              | [GitHub self-hosted runners REST API](https://docs.github.com/en/rest/actions/self-hosted-runners)                                                                                                                                                                      | The API introduces a coordinator-consumable reservation contract. |

## 4. Selected Technology Facts

These facts justify feasibility, not correctness or selection by themselves.

| Id   | Verified fact                                                                          | Source                                                                         | Revision trigger                                                                        |
|------|----------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| TF-1 | FastAPI can generate OpenAPI with JSON Schema projections from typed API declarations. | [FastAPI features](https://fastapi.tiangolo.com/features/)                     | The selected FastAPI version removes or materially changes this projection capability.  |
| TF-2 | Pydantic can generate JSON Schema from admitted model types.                           | [Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/) | The selected Pydantic version removes or materially changes this projection capability. |

```text
SchemaGenerationCapability -/-> SchemaCompleteness
SchemaGenerationCapability -/-> ConsumerConformance
```

Exact HTTP-model directionality, serialization, generated-client compilation,
and browser runtime admission remain requirement-owned product obligations.

## 5. Governance Premises

| Id   | Adopted premise                                                                                                                                                                                                 | Revision trigger                                                                                        |
|------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| CA-1 | Active requirement sources own product behavior; incidental implementation behavior has no independent authority.                                                                                               | The repository adopts another explicit behavior-authority hierarchy.                                    |
| CA-2 | An executable behavioral claim requires a requirement-bound native falsifier; prose, generated output, and structural admission are insufficient by themselves.                                                 | A replacement proof policy demonstrates equal or stronger falsification and is adopted.                 |
| CA-3 | Dynamic omission authority is absent until exact external production admission succeeds. Missing, stale, invalid, or scope-mismatched authority yields FullCI or an explicit failure, never selected execution. | The production-admission contract is replaced by an owner-approved stronger protocol.                   |
| CA-4 | Agentic Proofkit governs requirement ownership, routing, and evidence bindings; it is not runtime behavior or production authority.                                                                             | Proofkit receives separately specified runtime authority, with product witnesses and failure semantics. |

## 6. Authority Selection

Single ownership is a selected repository policy, not a theorem of logic. Let:

```text
S := one canonical premise owner plus references
R := replicated premise owners plus content-addressed equality enforcement
```

Both can be internally consistent. In the current repository there is one
publication boundary and no independently governed premise consumer. `S`
satisfies consistency with fewer duplicated facts and synchronization edges,
so `S` is preferred under the current context. A future independently published
consumer with its own governance can reopen this choice.

## 7. Non-Claims

These premises do not claim that:

- the premise set is a complete description of every future environment;
- Python, FastAPI, Pydantic, or TypeScript creates safety without contracts;
- generated OpenAPI is automatically complete or consumer-compatible;
- GitHub Actions workflows are fully dynamic at runtime;
- runner telemetry reserves capacity;
- package separation alone proves behavioral independence;
- agent output can justify omitted validation; or
- local proof implies provider wiring, deployment, enforcement, or production readiness.

When a premise becomes false or unknown, every dependent law and decision is
unproven until re-derived. Unknown premise state cannot silently retain dynamic
omission authority.
