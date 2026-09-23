# Deployment Entrypoint Admission Implementation Plan

Status: implementation plan

Date: 2026-09-07

## Scope

Implement the [entrypoint design](deployment-entrypoint-admission.md) on
`e29454d0b037e5be47574611421116dd7d21af7e`. Preserve independent D4 work and every
unrelated application, database, credential and published migration.

## Sequence

1. Add the UI-owned fixed root redirect and GET/HEAD, query, disabled-route and
   non-GET/unknown-route falsifiers to the existing HTTP owner.
   Include `/` in the packaged container smoke's exact route set and its argv
   contract; retain every existing route and the exact-set predicate.
   In Keycloak mode, gate both workbench paths before HTML using existing human
   session and read-role authority. Keep assets public and APIs unchanged;
   distinguish login redirect, forbidden credentials/roles and unavailable
   session storage. No provider or session creation occurs during this gate.
   Admit required issuer metadata and optional bounded session metadata in the
   callback, reject duplicate keys before Pydantic mapping, bind the issuer to
   settings before exchange, and derive OpenAPI from the same query model.
2. Add the provenance-only ping outcome and core grammar; keep HMAC, JSON bounds,
   event-family binding, durable claim and no-preparation guarantees. Evolve
   the ingestion profile to v2 and synchronize its packaged resource and wheel
   inspection owner. Do not relax repository-scoped outcomes.
3. Extend native ingestion, HTTP mapping and process-worker tests. Prove both
   valid app-level and repository ping; mutate core fields, action and every CI
   family; exercise duplicate/conflict/unavailable/cancellation outcomes.
   Add the three named ping guard mutants to the existing HTTP-admission suite
   and preserve its complete inventory and finite execution envelope.
   Its 16 cases require `16 * 2 * 5000 + 60000 = 220000` milliseconds under the
   existing budget formula. Synchronize the manifest and command envelope;
   retain the per-case bound and independently check the whole-job reserve.
   Browser controls must include a complete provider-shaped positive callback,
   isolated missing/duplicate/domain/issuer counterexamples, session expiry,
   revocation, insufficient roles, overload and dependency failure. Exercise
   GET/HEAD and both page paths. Root and page fixtures must contain an admitted
   index, asset and canonically serialized manifest so failures reach the
   intended operation. Distinguish read-only success from rejection with every
   other canonical role but no read role; all-role/empty-role cases alone do
   not prove the selected role is exact.
   Synchronize the exact HTTP model count and every downstream policy operand:
   inventory the callback model, allow only its provider-owned `session_state`
   validation name, and admit the unaliased Pydantic error type. Retain negative
   alias/module/policy-builder probes and the complete model-policy traversal.
   A passing mutation baseline is required before any killed-mutant claim.
4. Update current requirements, module specification and Proofkit routes. Link
   these new design/plan documents from the navigation index. Preserve prior
   design and implementation-plan bytes.
   Register both removed v1 proof resources through the exact-baseline
   retirement manifest; retain current successor witnesses. Correct current
   roadmap progress without closing external authentication or rollout gaps.
5. Run admitted static lint, type, import, documentation, requirement and
   Proofkit checks. Have one independent reviewer apply the current repository
   agent policy to the frozen candidate. Native behavioral, package and full
   checks run through GitHub, never locally.
6. Squash only an exact green candidate; verify postmerge, publish a new exact
   release, update only the Coordinator runtime and inspect its hardening and
   readiness again. Do not rerun completed database provisioning.
7. Verify the internal root redirect, unchanged public route isolation,
   incorrect-signature rejection and correctly signed provider-shaped ping.
   Finish Keycloak login/logout independently. Enable no App webhook until the
   ingress prerequisites hold; actual provider delivery is a separate receipt.

## Acceptance And Non-Claims

The old failing root and app-level ping counterexamples must pass while their
negative controls still reject. Every exposed new source/profile path must
have an owned witness route. A temporary environment probe, code review or
green CI does not imply another environment's admission or product completion.

Old Coordinator retirement happens only after the new runtime is verified:
stop exactly its obsolete services and delete only `ci_coordinator_dev` under
the user's explicit authorization. Never restart shared PostgreSQL or change
neighboring databases. Resume the D4 roadmap after this onboarding repair.
