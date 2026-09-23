# Platform-Owned Reusable Workflow Library

Status: proposed architecture decision

Last verified: 2026-07-16

## Decision

Create a platform-owned **source and release repository** for typed CI building
blocks, schemas, a compiler, reusable workflows, and composite actions. Do not
make it a runtime orchestration service and do not make a remote reusable
workflow the sole availability or fallback authority of a required gate. The CI
Coordinator repository may host one narrowly scoped immutable reusable workflow
that owns only OIDC minting and the plan HTTP exchange; it owns no target
execution, plan validation, stable gate, or FullCI command.

Target repositories should contain:

1. a small reviewed DSL document;
2. repository-owned triggers, permission ceilings, concurrency, environments,
   stable gate identity, and FullCI fallback;
3. compiler-generated static workflow artifacts committed by pull request;
4. full-SHA remote calls only where cross-repository call resolution is not the
   sole path to required validation; and
5. an independently runnable native validation path during remote-call adoption.

This is a hybrid: thin human-authored policy plus locally reviewable provider
artifacts, not hand-copied full workflows and not an opaque remote thin client.

## Decision Predicate

Let:

- `C` mean shared behavior has one reviewed source;
- `L` mean each repository retains trigger, permission, credential, gate, and
  fallback authority;
- `I` mean every external reference is immutable;
- `T` mean GitHub can validate the topology before execution;
- `F` mean FullCI remains reachable without coordinator authority;
- `R` mean repository owners can review the exact provider artifact;
- `D` mean provider drift is detectable.

```text
AdmissibleDesign := C and L and I and T and F and R and D
```

| Option                                      | C     | L     | I/T      | F        | R/D   | Result                                                           |
|---------------------------------------------|-------|-------|----------|----------|-------|------------------------------------------------------------------|
| Hand-copy complete workflows                | false | true  | possible | true     | weak  | Reject: divergence is structural.                                |
| Remote reusable workflow owns everything    | true  | false | possible | unproved | weak  | Reject: excess central authority and call-resolution dependency. |
| Coordinator emits workflow YAML at run time | false | false | false    | unproved | false | Reject: platform topology is already fixed.                      |
| Typed DSL compiles reviewed local artifacts | true  | true  | true     | true     | true  | Accept.                                                          |

The accepted option is least-authority because the platform library can propose
implementation but cannot change a target trigger, secret scope, required
status, or active fallback without a target-repository review.

The privileged plan requester is a justified exception to purely local
materialization because pull-request-controlled workflow bytes cannot safely
hold `id-token: write`. Its authority is admissible only under:

```text
ImmutableFullShaRef
and NoTargetCheckout
and Permissions = {id-token: write}
and Audience = ExactPlanUrl
and Output in {bounded signed-plan chunks, bounded fallback reason}
```

## Platform Constraints

GitHub resolves a reusable workflow through static `jobs.<job_id>.uses`; that
field does not accept contexts or expressions. Dynamic matrices can vary work
inside a predeclared topology, but cannot select arbitrary workflow files at
run time. Called workflows receive only the caller's maintained-or-reduced token
permissions, secrets cross only declared call edges, and a call chain has a
finite nesting limit.

Therefore:

```text
DynamicData within StaticTopology is possible
RuntimeGeneratedTopology is not generally possible
```

The DSL must consequently compile **before merge** into ordinary GitHub YAML.
It may select only versioned templates, typed inputs, execution profiles, and
provider signals from an admitted catalog. Arbitrary shell, arbitrary `uses`,
and arbitrary secret names are outside the language.

Reusable workflows are not the smallest unit for repeated steps because they
are called as jobs. Use:

- reusable workflows for coherent multi-job lanes such as backend test, build,
  or infrastructure validation;
- composite actions for repeated step sequences;
- target-owned scripts for repository behavior that is already part of the
  repository's tested product surface.

## Authority Split

| Concern                                                                  | Owner                                                |
|--------------------------------------------------------------------------|------------------------------------------------------|
| Template source, schema, compiler, compatibility tests, release manifest | Platform library repository                          |
| Event triggers and path-filter prohibition on the required gate          | Target repository                                    |
| Workflow and job permission ceiling                                      | Target repository                                    |
| Secret values, environments, and credential approval                     | Target repository or organization admin              |
| Runner labels, groups, services, and execution profile binding           | Joint typed contract; target approves binding        |
| Stable required-check identity                                           | Target repository and provider ruleset admin         |
| Runtime plan and matrix data                                             | CI Coordinator within a statically admitted envelope |
| FullCI command and outage fallback                                       | Target repository                                    |

Use explicit named secrets rather than `secrets: inherit`. Pin remote workflow
and action references to full commit SHAs. A release tag may aid discovery but
must not become execution authority.

## Fallback Rule

A remote reusable workflow can fail before its jobs execute because access,
reference, or provider validation fails. A fallback step inside that called
workflow cannot prove recovery from such a failure.

```text
FallbackAuthority(remote call) requires CallResolved(remote call)
not CallResolved => internal fallback is unreachable
```

Until live provider chaos evidence proves a stronger mechanism, keep the native
Full Check independently runnable and required during adoption. For later
optimized enforcement, materialize the critical workflow from the central
source into the target repository or retain an independently triggered local
fallback. Safety may accept a failed closed gate; availability may not call it
FullCI fallback without execution evidence.

After call resolution, the narrow plan requester must totalize OIDC and network
failures into bounded outputs so the target-owned plan job can select FullCI.
This strengthens runtime fallback but does not falsify the pre-resolution rule:

```text
CallResolved and RequestFailure -> TargetLocalFullCI
not CallResolved                -> ProviderHardFailure
```

## DSL Output Contract

For source `S`, compiler `V`, catalog `C`, and generated workflow `W`:

```text
Compile(S, V, C) = W
and Recompile(S, V, C) = W
and ValidateGitHubSyntax(W)
and ValidateSecurityPolicy(W)
and RenderedAuthority(W) <= ReviewedAuthority(S, C)
```

The generated header records source hash, compiler version, catalog digest, and
target repository. CI rejects manual output drift and non-reproducible
generation. Updates arrive as normal reviewed pull requests; the coordinator
does not write directly to protected branches.

## Migration Sequence

1. **Library foundation.** Define execution-profile, provider-signal, secret,
   runner, service, and stable-gate schemas. Build deterministic generation,
   actionlint/zizmor gates, fixture repositories, signed release manifests, and
   automated update pull requests.
2. **reporting-service archetype.** Model frontend, backend, and AWS-bearing
   infrastructure as separate typed lanes. Preserve Full Check and prove that
   credential omission never broadens authority.
3. **pilot.** Import its coverage map as governed witness evidence and
   bind GitHub-hosted and self-hosted work to distinct execution profiles.
   Replace fixed test partitions only after bounded duration data is admitted.
4. **extension-service archetype.** Model runner groups and labels, then test
   telemetry-driven shard count without changing selected coverage.
5. **Optional full redesign.** Compare generated workflows with each frozen
   baseline. Adopt only when coverage and fallback are no weaker, authority is
   no greater, gate identity is stable, and operational complexity is lower.
6. **Enforcement review.** Require embedded same-run observation, zero unsafe
   omissions, provider fallback and rollback exercises, and explicit owners as
   specified by [Production Admission](../architecture/cross-cutting/production-admission.md).

## Non-Claims

This decision does not prove that a separate workflow-library repository has
been created, that cross-private-repository access is configured, that
pre-resolution remote-call failures execute a fallback, or that one library can
erase repository-specific execution profiles. It does not authorize dynamic
omission or replace the existing
[GitHub Actions Semantics](../architecture/cross-cutting/github-actions-semantics.md)
and [Bootstrap Workflow Contract](../bootstrap-workflow-contract.md).

## Platform Sources

- [Reuse workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
- [Reusing workflow configurations](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)
- [Workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [Actions limits](https://docs.github.com/en/actions/reference/limits)
