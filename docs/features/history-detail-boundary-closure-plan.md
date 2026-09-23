# History Detail Boundary Closure Plan

Design: [History Detail Boundary Closure](history-detail-boundary-closure.md).
Base: `0fde9d0937243ab3e9ce658abd0ddb18832b1507`.

## Changes And Acceptance

1. Normalize the bounded detail job tuple in `ci_history_provider.py` at its
   existing domain construction. Add same-page and cross-page permutation
   controls plus a valid-detail duplicate-job refusal in the provider tests.
2. Add one `archived_detail` factory to the existing archive test support.
   Replace duplicate builders and parameterize the application forwarding
   witness across all three collection lanes.
3. Extend existing PostgreSQL recheck cases with a nonempty payload: successful
   first import/replay, detail-only capacity refusal and terminal lease expiry.
   Observe child bytes, exact quota, retained anchor and full rollback.
4. Supply a different, independently validated same-length payload to retained
   replay. Verify original parent/child, jobs, policy, quota and revision.
5. Add successful HTTP detail responses for populated and absent payloads,
   preserving exact query binding, no-store and existing response vocabulary.
6. Link this increment and update current Proofkit routing plus the roadmap
   projection. Preserve all pre-existing designs and implementation plans.

## Verification

Run owner-admitted local lint/type/generated-contract and proof-routing checks.
Behavioral tests run only in repository-owned GitHub CI. The passing baseline
master run does not qualify these changes. Freeze the grouped candidate, use
the bounded independent control permitted by `AGENTS.md`, and repair only
confirmed blockers before the final native qualification. Do not weaken gates
or rewrite a published branch.

No schema, ACL, retention, client workflow or provider-permission change is
required. Live pilot target recovery remains a separately admitted next step after
corporate access and the actual deployed artifact have been revalidated.
