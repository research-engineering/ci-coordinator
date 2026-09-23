# Dynamic CI Enforcement Design

Status: local enforcement consumer implemented; external admission pending

Date: 2026-07-29

## 1. Feature Question

Can CI Coordinator run only the checks required by a specific diff while
remaining correct if the coordinator, graph, diff, or agent is wrong?

Answer: yes, but only through a static gate-owning target workflow, a
deterministic planner, a verifier, replayable proofs, and FullCI fallback. The
target may use generated witness shards or an existing native static job set.

## 2. Authority Boundary

This document owns enforcement rollout. Pure planning behavior is owned by
`docs/architecture/modules/planning-core.md`; execution behavior is owned by
`docs/architecture/modules/execution-orchestration.md`.

Dynamic CI enforcement must not claim production readiness until the target
adapter, diff context, graph context, store, replay, shadow metrics, and
rollback gates exist.

The generated bootstrap fixture is one fail-closed adapter, not the product
boundary. An existing workflow may instead adopt `native-job-set` while
retaining its commands and aggregate gate. Local shadow and rollback mechanisms
exist, but they do not enable production omission without external non-vacuous
evidence. In-workflow signature validation is a target safety gate, not
enforcement approval.

## 3. Formal Contract

Let:

- `D` be the complete diff.
- `G` be a fresh dependency graph.
- `P` be the validated CI policy.
- `C` be the workflow/check catalog.
- `I = (repoEpoch, D, G, P, C)` be the planning input.
- `Plan(I)` be the deterministic planner output.
- `Verify(I, Plan(I), Advice?)` be the verifier output.

Safety law:

```text
Omit(check) is allowed only if Proof(check, I) is valid.
Invalid(I) => FullCI.
Unavailable(Coordinator) => FullCI by target-workflow timeout.
Advice cannot decrease Verify(I, Plan(I)).
```

Local verification cannot create production authority. The enforcing
composition therefore consumes one externally signed production-admission
receipt and projects an authority bounded to its exact repository scopes:

```text
SelectedExecutionAuthorized(request, receipt) :=
  ProductionAdmissionReceiptValid(receipt)
  and ExactAuthorityAndSubjectDurablyRegistered(receipt, request)
  and request.repositoryScope in receipt.repositoryScopes
  and DatabaseClock < receipt.expiresAt
  and request.configEpoch = ActiveConfigEpoch(request.repositoryScope)
  and no active subject force-FullCI override
  and no latched repository omission disable
  and signedPlan.productionAdmissionReceiptId = H(receipt)
```

No receipt, an invalid or expired receipt, a scope mismatch, or an operator
override produces FullCI. A boolean process flag is not admission evidence and
cannot enable omission.

The ordering relation is pointwise validation coverage over the complete check
catalog. For every check `c`:

```text
FullCI(c) >= selected(c, full) >= selected(c, standard)
>= selected(c, targeted) >= omitted(c)
```

For two plans `P` and `Q`, `P >= Q` means `P(c) >= Q(c)` for every check `c`.
Plans that are stronger for one check and weaker for another are not ordered;
the verifier must reject any agent advice that creates a weaker point for any
check.

Agent advice is valid only if final coverage is greater than or equal to the
deterministic plan.

## 4. Why This Is The Best Shape

Claim: a static gate-owning target workflow plus deterministic planning
dominates direct dynamic workflow generation.

Proof:

1. GitHub required checks need stable identities.
2. A static required workflow provides a stable identity.
3. Dynamic generation without a static gate cannot guarantee that GitHub sees a
   required status for every PR, push, or merge group.
4. Coordinator downtime must not prevent validation from running.
5. A target-owned timeout can run FullCI without Coordinator availability.
6. Therefore the static target workflow and gate are the best availability and
   GitHub-semantics boundary; a generated bootstrap is only one implementation.

Claim: deterministic omission dominates agent-only omission.

Proof:

1. Safe omission is a universal claim: no changed artifact affects the omitted
   check.
2. Universal claims require complete known inputs or explicit fallback.
3. Agent analysis is probabilistic and prompt-sensitive.
4. Therefore agent analysis cannot be the proof authority for omission.
5. It remains valuable as a monotonic risk escalator.

## 5. Enforcement Rollout Requirements

This section defines enforcement requirements, not delivery order.

1. Configuration:
   - validate check ids, stable required names, workflow files, risk classes,
     credential profiles, fixture profiles, responsibility surfaces, and
     fallback timeout.
   - reject duplicate stable required names and unsupported glob patterns.

2. Diff context:
   - support `pull_request`, `push`, and `merge_group`.
   - persist base SHA, head SHA, file status, previous names, patch hashes,
     pagination metadata, API source, and truncation state.
   - map incomplete or ambiguous diff to FullCI.

3. Graph context:
   - ingest dependency graph from repository-owned artifact.
   - validate graph freshness against repository epoch, trusted generator
     metadata, head SHA, and invalidation paths.
   - reject dangling edges, unsafe paths, unknown changed paths, and graph risk
     classes outside the policy domain.

4. Target workflow:
   - keep one stable required workflow name.
   - request signed plan from CI Coordinator.
   - validate plan signature, key id, head SHA, expiry, run identity, and schema
     version.
   - run selected matrix entries only for explicit valid non-fallback payloads.
   - run FullCI on timeout, invalid plan, stale input, or verifier error.

5. Shadow mode:
   - compute plans but do not omit real checks.
   - compare planned omissions against full validation outcomes.
   - record unsafe candidates in the audit ledger.

6. Limited enforcement:
   - enable only for low-risk surfaces with zero unsafe shadow findings.
   - require rollback switch and alerting.

7. Runtime authority transfer:
   - admit a bounded canonical Ed25519-signed receipt;
   - bind it to the deployed artifact digest, source commit, environment,
     rollout profile, and exact repository scopes;
   - require non-vacuous same-artifact deployment, provider, fallback, shadow,
     rollback, stable-gate, and owner-approval evidence;
   - include the derived receipt identity in every selected signed plan;
   - keep subject force-FullCI and the latched repository omission-disable
     controls independent from receipt validity so rollback needs no code
     deployment;
   - register exact verified authority and scope bindings before provider
     allocation, then recheck authority, database time, epoch, and controls in
     the selected-plan insertion transaction.

## 6. Production Gates

- FullCI fallback executes when Coordinator is unavailable.
- Merge queue repositories have `merge_group` support before enforcement.
- Signed plan requests are bound to an allowlisted GitHub Actions workflow
  identity, not only to repository and ref claims.
- Every omission has a proof object linked to the audit ledger.
- Shadow unsafe omission rate is zero for the enabled surface.
- Required check identity is stable and bound to the expected source.
- Operator rollback disables omission without code deploy.
- Every selected signed plan identifies the exact production-admission receipt
  that authorized its repository scope.
