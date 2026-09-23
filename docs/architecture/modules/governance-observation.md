# Governance Observation Module Specification

Status: implementation contract for the read-only effective-rules slice

Date: 2026-07-26

Owner: `governance_observation`

## 1. Owned Invariant

The module owns a bounded, non-persistent observation of active GitHub rules
that apply to one exact repository's current default branch.

```text
AuthorizedExactScope
and TerminalBoundedEffectiveRuleTraversal
and IdentityStableAcrossRead
=> UnbaselinedGovernanceObservation
```

It does not own provider credentials, HTTP admission, persistence, baseline
approval, drift policy, enforcement, release readiness, or omission authority.

## 2. Boundary Algebra

```text
static or fresh browser read grant
  -> governance_observation service
  -> GitHub installation adapter
  -> strict bounded provider decoder
  -> immutable domain observation
  -> no-store HTTP projection
  -> bounded frontend runtime admission
```

Dependencies point inward:

```text
api/http -> governance_observation <- integrations/github
runtime composition -> all three
```

The domain imports no FastAPI, GitHub transport, environment, SQL, browser, or
clock implementation.

## 3. Domain Model

```text
GovernanceRepository :=
  exact RepositoryScope
  immutable provider owner id
  current owner/name assertion
  current default branch

EffectiveRule :=
  ruleType
  rulesetSourceType
  rulesetSource
  rulesetId
  complete canonical provider-rule JSON bytes

GovernanceObservation :=
  repository
  providerApiVersion
  observedAt
  consistency = best_effort
  baselineState = unbaselined
  canonically ordered unique rules
  stateDigest
```

`stateDigest` is recomputed from every field except `observedAt`,
`consistency`, and `baselineState`. Rule ordering uses canonical provider bytes,
so provider page order cannot change identity.

## 4. Provider Protocol

The adapter:

1. obtains one installation-authenticated client after use-case authorization;
2. reads `/repositories/{repository_id}`;
3. binds numeric id, owner id, owner/name, full name, and default branch;
4. proves that the bounded Git branch and complete percent-encoded provider
   identity form one admitted credential-plane request path;
5. reads
   `/repos/{owner}/{name}/rules/branches/{default_branch}?page=N&per_page=100`;
6. follows one exact next-page trajectory to admitted terminal evidence within
   fixed page, item, response-byte, canonical-byte, depth, and node limits;
7. decodes every item and rejects duplicates;
8. re-reads `/repositories/{repository_id}`; and
9. succeeds only if the complete repository identity is unchanged.

Caller or task cancellation propagates and produces no observation. Provider
failure, malformed provenance, pagination contradiction, overflow, or identity
mismatch returns one typed unavailable outcome and no observation.

## 5. Authorization

Machine and browser requests first authenticate an admitted Keycloak principal
with the `read` role, then intersect that authority with the deployment-owned
exact installation-and-repository scope. Only then may the installation adapter
run. The GitHub App proves provider visibility; it does not mint the Keycloak
role, and the Keycloak role does not widen the App installation scope.

```text
CatalogVisibility -/-> GovernanceReadAuthority
KeycloakReadRole and ExactAppScope -> exact scope only
GovernanceReadAuthority -/-> GovernanceMutationAuthority
```

## 6. Resource Bounds

- one repository before-read and one after-read;
- 100 rules per page;
- at most 10 pages and 1,000 rules;
- at most 1 MiB provider JSON per page;
- at most 2 MiB aggregate canonical rule bytes;
- at most 8 MiB for the outer browser response after canonical rule text and
  its repeated metadata are JSON-escaped;
- at most 512 UTF-8 bytes for the default branch and 4,096 ASCII bytes for the
  complete percent-encoded request path;
- at most 16 JSON levels and 16,384 JSON nodes per rule page;
- bounded scalar text and JSON-safe integers only.

Bounds are implementation constants in the first slice. Raising them requires
validated runtime configuration and equivalent resource witnesses.

## 7. Failure Algebra

| Fact                                                    | Outcome                                  |
|---------------------------------------------------------|------------------------------------------|
| actor or scope denied                                   | forbidden before provider I/O            |
| provider 404                                            | not found                                |
| documented rate limit                                   | rate limited with bounded retry metadata |
| provider timeout, TLS, DNS, or non-success              | unavailable                              |
| caller or task cancellation                             | propagated; no observation               |
| missing API-version provenance                          | provider binding mismatch                |
| provider identity cannot form the admitted request path | malformed provider response              |
| malformed JSON, rule, pagination, or canonical form     | malformed provider response              |
| repository identity changes before/after                | provider binding mismatch                |
| page or aggregate bound exceeded                        | observation limit exceeded               |
| terminal traversal reports zero rules                   | valid empty unbaselined observation      |

## 8. File Ownership

| Surface                                                          | Responsibility                          |
|------------------------------------------------------------------|-----------------------------------------|
| `governance_observation/model.py`                                | immutable state, digest, and outcomes   |
| `governance_observation/ports.py`                                | authorizer and reader capabilities      |
| `governance_observation/service.py`                              | authorization-first orchestration       |
| `integrations/github/governance_observation*.py`                 | provider requests and decoding          |
| control-plane HTTP authentication                                | exact principal and read-role admission |
| `api/http/routers/governance_observation.py`                     | HTTP admission and projection           |
| `frontend/src/api/governanceObservation/`                        | runtime admission and transport         |
| `frontend/src/features/workbench/GovernanceObservationPanel.tsx` | truthful repository-detail rendering    |

No repository abstraction is introduced because this slice has no local
persistence. No use-case class beyond the service is introduced because one
authorization-plus-read operation has one reason to change.

## 9. Non-Claims

The module does not prove a point-in-time snapshot or stable rule membership
across GitHub pages, ruleset bypass visibility, classic branch protection,
workflow integrity, expected policy, compliance, drift, durable history,
provider enforcement, release readiness, or CI omission safety.
