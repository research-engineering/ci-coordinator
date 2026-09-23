# Governance Observation Implementation Plan

Status: implemented; acceptance remains governed by Section 4

Date: 2026-07-26

Requirements: `REQ-CI-RUNTIME-023`, `REQ-CI-UI-011`

Design owners:
[governance observation](../architecture/modules/governance-observation.md)
and
[governance, drift, and release evidence](governance-drift-release-evidence.md)

## 1. Objective

Deliver one read-only, non-persistent observation of the active GitHub rules
returned along one terminal bounded pagination trajectory for an exact
repository's current default branch. The result must terminate within fixed
bounds, preserve every rule returned on that trajectory, be content-addressed,
explicitly best effort and unbaselined, and be incapable of authorizing
compliance, mutation, release, or CI omission.

## 2. Dependency Order

```text
requirement and design
  -> pure domain contract
  -> bounded provider adapter
  -> authenticated application and HTTP projection
  -> generated frontend contract
  -> frontend admission and read-only rendering
  -> Proofkit routing
  -> composed local witnesses
```

Each node depends only on earlier contracts. Reversing an edge would make a
domain decision depend on transport or presentation details.

## 3. Implementation Steps

1. Add the immutable `governance_observation` state, failures, ports, and
   authorization-first service.
2. Add exact GitHub request admission, repository rebinding, finite
   pagination, strict JSON decoding, canonical rule preservation, and typed
   provider failures.
3. Add static-operator and fresh browser-grant composition plus one no-store,
   exact-scope FastAPI read route.
4. Generate the OpenAPI projection and immutable TypeScript transport types
   from the served route.
5. Add capability-owned response admission, backend-compatible canonical
   identity verification, bounded transport, stale-request cancellation, and
   read-only progressive disclosure.
6. Bind every proof owner and risk-appropriate falsifier to both requirements
   and close all selective-plan edges.
7. Run focused witnesses, then the repository's complete local quality plan
   and browser accessibility witness.

## 4. Acceptance

```text
Accepted iff
  requirements admitted
  and import boundaries admitted
  and provider and domain falsifiers pass
  and OpenAPI generation is reproducible
  and frontend schema and digest falsifiers pass
  and browser accessibility and viewport witnesses pass
  and Proofkit reports no unknown changed-path edge
  and complete local quality passes on unchanged bytes
```

Failure of any term blocks the local completeness claim. Provider behavior,
deployment, baseline approval, compliance, enforcement, release readiness,
and omission safety remain external or future claims.

## 5. Revision Conditions

Revise this plan if implementation requires persistence, provider write
permission, another public endpoint, a configurable resource bound, or a
frontend action beyond refresh. Each condition changes an owner or trust
boundary and therefore invalidates the current dependency graph.
