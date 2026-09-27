# Signed Plan Envelope Admission Implementation Plan

Status: implementation sequence
Date: 2026-09-27

Owner decision: [signed-plan-envelope-admission.md](signed-plan-envelope-admission.md).
Requirement authority remains in the existing plan-issuance/bootstrap owners.

1. Freeze the admitted base and current helper, kernel, target and caller bytes.
2. Preserve signer implementation and public export. Add required expected_key_id,
   canonical verification and same-string integer time guards conjunctively with
   the old Python lifetime checks.
3. Migrate every in-repository helper/Node call to explicit trusted kid and clock.
4. Add one owner-defined vector file with literal canonical bytes and outcomes.
   Sign independently inside native tests; preserve all old assertions.
5. Check malformed key/time/signature, each independent operand, sub-ms boundaries,
   aware ZoneInfo and the deliberate same-zone-fold countermodel.
6. Update only the current module/bootstrap contracts and these new documents.
   Root handles requirements, route/risk hashes and generated outputs separately.
7. Run scoped canonical-environment Ruff/format/mypy and static AST/hash/JSON
   checks. Never substitute them for required GitHub behavioral execution.
8. Freeze exact paths and hashes in the external writer handoff. Independent
   review, exact native Python/Node cases, bundle positives and admitted causal
   mutation witnesses remain GitHub prerequisites.

No source change to kernel, JavaScript, signing bytes, persisted replay, runtime
configuration or target policy is authorized. No local test/collection/Node,
container/build/install, provider, commit/push/rebase or new agent is permitted.
