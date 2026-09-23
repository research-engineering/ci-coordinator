# Target Repository Adoption Playbook

Status: guarded adoption playbook

Date: 2026-07-17

## 1. Current Capability Boundary

The repository implements a connected control plane with two authority paths.
`non_enforcing` authenticates a target GitHub Actions run, computes and verifies
a candidate, persists evidence, and returns a signed FullCI envelope.
`enforcing` additionally consumes an externally signed production-admission
receipt, registers its exact authority durably, and may issue selected plans
only for admitted repository scopes and subjects.

The target bootstrap maps selected profiles to reviewed static jobs and exposes
one stable aggregator. The service does not configure rulesets, mint external
evidence, suppress native workflow triggers, or authorize production omission
by itself.

```text
NonEnforcingRuntime => SignedFullCIFallback
EnforcingRuntime => (ReceiptGatedSelectedPlan or SignedFullCIFallback)
LocalImplementation => NoExternalOmissionAuthority
```

Consequently, any rollout instruction that removes native validation before
external admission is unsound. This playbook keeps existing CI and required
checks unchanged until every external admission predicate is proven.
The production-admission specification owns the required external conjunction;
this playbook owns the adoption procedure only.

## 2. Responsibility Split

| Layer                          | Owner                                    | Required authority                                |
|--------------------------------|------------------------------------------|---------------------------------------------------|
| Library and CLI adoption       | Repository developer                     | Local repository write access                     |
| Repository proof configuration | Repository developer and reviewer        | Pull-request merge access                         |
| Provider wiring                | Platform or repository administrator     | Actions, secrets, app, and ruleset administration |
| Production omission            | Product, platform, and repository owners | Explicit joint approval after evidence            |

Without provider wiring, the valid state is `local proof only`. Missing admin
authority must not block local development and must not be represented as
provider readiness.

## 3. Adoption Invariant

Let:

```text
N = existing native CI remains enabled and required
B = bootstrap workflow is installed without path filters
I = immutable coordinator artifact is deployed
A = GitHub App, OIDC, secrets, and repository identity are admitted
D = PostgreSQL migration and principal attestation pass
F = signed fallback is verified end to end
S = non-vacuous shadow evidence contains no unsafe omission
R = rollback is rehearsed
G = stable required-check and merge-queue behavior is proven
O = named owners approve enforcement
Q = independent full-row target-authority relation closes for the same epoch
E2 = one valid v2 receipt binds Q and every required evidence class to the same subject
C = predecessor grants, plans, leases, target executions, and old replicas
    are drained and the exact successor scope generation is active
```

The current adoption gate is:

```text
SafeAdoption := N and B and I and A and D and F
FirstTargetProductionOmission :=
  SafeAdoption and S and R and G and O and Q and E2 and C
```

`SafeAdoption` does not imply production omission. The current runtime uses
v2 receipt admission and generation-fenced authority; implementation does not
establish the external premises of `FirstTargetProductionOmission`. Follow
[Stage And Activate Production Authority](how-to/stage-and-activate-production-authority.md)
only after its qualification prerequisites are met. Until the conjunction is
proven and the exact scope generation is activated, native required CI and
FullCI fallback remain in force.

## 4. Phase 0: Inventory And Rollback

Before changing the target repository:

1. Record every workflow trigger, stable job name, required check, ruleset, and
   merge-queue condition.
2. Identify the repository administrator, platform owner, application owner,
   and rollback owner.
3. Capture the last known-good native CI behavior on pull request, push, merge
   group, and manual dispatch events that the repository uses.
4. Prepare one revert that removes coordinator wiring without changing native
   CI.

Exit proof: the target's pre-adoption CI contract and rollback owner are
reviewable artifacts. Failure to inventory any required check stops adoption.

## 5. Phase 1: Deploy The Non-Enforcing Service

Platform administration must provide:

- an immutable image digest built from the reviewed source commit;
- PostgreSQL 18.6 with migrations at the admitted head and least-privilege
  runtime grants;
- TLS ingress for the plan and webhook routes;
- GitHub App installation, webhook secret, private key, and allowed repository
  scope;
- plan-signing key custody and a distributed public verification key;
- an OIDC audience and exact workflow-ref allowlist;
- readiness, metrics collection, alert ownership, and bounded rollback.

Exit proof: the deployed artifact reports ready, the exact schema and principal
attestations pass, and no secret appears in diagnostics. Local container success
alone is insufficient evidence.

## 6. Phase 2: Install The Bootstrap In A Sandbox

Use the reviewed bootstrap fixture as the source, but bind every repository,
installation, workflow, key, and service URL explicitly. Do not add path
filters. Keep all native workflows and required checks unchanged.

Exercise pull request, push, merge group when enabled, and manual smoke paths.
For every event, verify:

1. OIDC identity binds the repository, workflow ref, run, attempt, and commit.
2. The signed envelope binds the same request and has an admitted key, TTL, and
   signature.
3. The non-enforcing service chooses FullCI even when the candidate proves a
   smaller plan.
4. Missing URL, credentials, key, malformed response, timeout, or service
   outage also chooses FullCI or fails the stable aggregator closed.
5. Exactly one stable aggregator reports the final bootstrap result.

Exit proof: fallback behavior is demonstrated on the deployed artifact, not
inferred from unit tests.

## 7. Phase 3: Shadow Evidence

Activate an admitted repository policy only after configuration registration,
review, CAS activation, and rollback have been exercised. Continue running all
native validation. Collect baseline-versus-candidate reconciliation evidence
for representative changes, including no-op, source, test, dependency,
workflow, configuration, migration, security-sensitive, and unknown changes.

The window is admissible only when it is non-vacuous, spans the agreed minimum
duration and sample cardinality, and records zero unsafe omissions. A missing
observation, provider ambiguity, replay mismatch, or reconciliation timeout is
FullCI evidence, not a successful optimized sample.

## 8. Phase 4: Provider Administration Review

An administrator, not the library adopter, reviews:

- GitHub App permissions and repository scope;
- OIDC subject and workflow-ref restrictions;
- Actions variables, secrets, environments, and fork behavior;
- required checks, merge queue, rulesets, and emergency bypass ownership;
- artifact provenance and signing-key rotation;
- alerting, retention, capacity, and rollback controls.

No existing required native check is removed in this phase. The administrator
must prove the exact `Dynamic CI Bootstrap` aggregator identity and merge-queue
behavior before it can replace any native required-check identity.

## 9. Receipt-Gated Enforcement Transition

The local enforcement consumer is implemented, but production omission still
requires the following external facts to be true for one exact artifact and
repository scope:

```text
ProductionEligibleArtifact
and ValidSuccessorProductionAdmissionReceipt
and SignedSelectedPlanEndToEnd
and CoveragePreservingExecution
and StableProviderConclusion
and BoundedFailoverToFullCI
and FirstTargetProductionOmission
```

Start the exact artifact in `enforcing` for the first production target only
after the successor conjunction holds and the v2 receipt signer binds it,
including the independent target-authority relation, to the admitted evidence.
Exercise selected static jobs and FullCI failure paths while native checks
remain required. Removing redundant native triggers or checks is a separate
two-phase provider cutover, not a continuation of the receipt epoch that
authorized shadow execution:

1. Require the stable aggregator while retaining every existing native required
   check, then prove merge-queue and FullCI fallback behavior for that overlap
   epoch.
2. Record an owner-approved transition intent for the exact post-change
   workflow and provider domains. Latch `disable_omission`, drain every issued
   selected plan, lease, and target execution, and prove the stable aggregator
   is serving FullCI. Keep the bootstrap workflow, stable aggregator, and FullCI
   path intact.
3. Apply the repository-owned removal while the latch remains set. The prior
   relation receipt remains cryptographically valid for its old epoch but has no
   operational authority under the latch.
4. Independently observe the post-change target and provider domains, close a
   new total target-authority relation, and issue a receipt for that exact epoch.
5. Re-enable selected execution only by atomically activating the new receipt
   and cutover generation and clearing the latch.

If any step fails, the latch remains set, the stable aggregator continues
FullCI, and the retained overlap state or reviewed inverse change restores the
previous provider configuration. No stale pre-change receipt may authorize the
post-change provider state. A provider mutation performed outside this protocol
invalidates the production-safety claim and requires an immediate FullCI
incident response; receipt expiry alone is not a revocation mechanism.

## 10. Rollback

At any current-stage failure:

1. Leave native CI and required checks in their pre-adoption state.
2. Disable or remove the bootstrap wiring.
3. Disable the repository policy or uninstall the GitHub App if trust wiring is
   implicated.
4. Preserve audit and reconciliation evidence for diagnosis.
5. Rotate credentials if confidentiality or integrity may be affected.

Because current adoption never removes native validation, rollback does not
need to reconstruct CI coverage.

## 11. Explicit Non-Claims

This playbook does not claim a deployment exists, provider wiring is complete,
GitHub rulesets are configured, shadow evidence has been collected, a
production-admission receipt has been issued, or omission is enabled. Static
selected-job execution is implemented by the bootstrap contract; installation,
provider conclusions, required-check administration, and production approval
remain external facts requiring their own evidence.
