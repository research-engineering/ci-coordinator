# Provider Inventory Authorization

Status: accepted and implemented

Date: 2026-09-02

## Context

A GitHub App private key can authenticate the App across every account where it
is installed. A Keycloak role proves control-plane identity, but does not prove
that a repository belongs to the deployment scope. Neither authority alone may
grant repository inventory access.

The UI and machine API must discover repositories without requiring numeric ids
from a developer, while preserving the distinction between provider visibility
and coordinator authorization.

## Decision

Admit provider inventory through the conjunction of three independent facts:

1. a Keycloak human session or workload access token with the exact `read` role;
2. a deployment-owned installation allowlist; and
3. current read-only GitHub App evidence for that installation.

Exact workbench access adds a fourth fact: the immutable
`(installation_id, repository_id)` must belong to the deployment workbench
scope.

```text
BrowseInstallations(P) :=
  AdmittedKeycloakPrincipal(P)
  and HasRole(P, read)

BrowseRepositories(P, I) :=
  BrowseInstallations(P)
  and I in InventoryInstallations
  and CurrentAppInstallation(I)

UseWorkbench(P, I, R) :=
  BrowseRepositories(P, I)
  and (I, R) in WorkbenchScopes
  and RepositoryAccessibleToInstallation(R, I)
```

The predicates are intentionally non-equivalent:

```text
ProviderVisible(I, R) does not imply UseWorkbench(P, I, R)
HasRole(P, read) does not imply ProviderVisible(I, R)
UseWorkbench(P, I, R) does not imply RepositoryAttestation(P, I, R)
```

An empty installation allowlist is valid and causes no provider I/O. It supports
local, library-only, and not-yet-wired deployments without inventing catalog
authority.

GitHub reviewer OAuth is admitted only by the separate proposal-bound
repository-attestation flow. Break-glass and target-workflow Actions OIDC
credentials cannot list inventory. No credential-plane union or fallback is
permitted.

## Alternatives

| Alternative                                                     | Rejected because                                                                                                                      |
|-----------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| List every App installation for any authenticated principal     | Identity is not deployment scope and may leak repository metadata.                                                                    |
| Treat App installation visibility as workbench authorization    | Provider access does not establish coordinator policy ownership.                                                                      |
| Derive installation browsing from one exact repository scope    | Authority to one repository does not entail authority to enumerate its installation.                                                  |
| Use repository names as authorization keys                      | Names are mutable; authorization requires stable ids.                                                                                 |
| Retain a GitHub user token for general administration           | It couples administrator identity to repository ownership, creates long-lived provider authority, and duplicates Keycloak governance. |
| Put any provider or Keycloak token in the browser               | Browser code is not a credential custodian; opaque same-origin sessions preserve the trust boundary.                                  |
| Require numeric ids in the primary UI                           | Stable transport identity is not a usable discovery experience.                                                                       |
| Persist all provider repositories before the first catalog read | Cache invalidation, deletion, and migration semantics are unearned for the bounded read capability.                                   |

## Consequences

- Keycloak authenticates the caller and supplies exact operation roles;
- deployment configuration bounds installations and exact workbench scopes;
- GitHub App credentials remain server-side and prove current provider facts;
- repository names are presentation data while ids remain canonical identities;
- inventory, workbench, attestation, config activation, and provider enforcement
  retain separate authorities; and
- a future webhook-backed durable inventory may replace the read model only if
  it preserves the same conjunctive admission predicate.

## Primary Evidence

- GitHub requires a JWT for authenticated-App installation endpoints:
  <https://docs.github.com/en/rest/apps/apps>.
- GitHub lists installation-accessible repositories through an installation
  token and paginates at most 100 items per page:
  <https://docs.github.com/en/rest/apps/installations>.
- GitHub App private keys grant access across installed accounts and must be
  secured as broad credentials:
  <https://docs.github.com/en/apps/creating-github-apps/about-creating-a-github-app/best-practices-for-creating-a-github-app>.

Evidence was revalidated on 2026-09-02 against the provider contract pinned by
the runtime profile. Live availability remains external evidence.

## Non-Claims

This ADR does not define generic multi-provider RBAC, grant organization-admin
permissions, authorize installation changes, attest repository ownership,
activate configuration, mutate provider settings, or authorize CI omission.

## 2026-09-08: Explicit App-Wide Read Grant

The preceding installation allowlist is now the default `restricted` mode,
not the only representable deployment grant. An explicit `app` mode admits
bounded read visibility across current installations of this exact App. This
updates only the deployment-scope conjunct:

```text
InventoryPermits(I) := Mode = app OR (Mode = restricted AND I in InventoryIDs)
BrowseRepositories(P, I) := BrowseInstallations(P) AND InventoryPermits(I)
                            AND CurrentAppInstallation(I)
```

All authentication, role, exact provider-identity and independent workbench
scope predicates above remain required. App mode rejects nonempty InventoryIDs;
restricted empty remains deny-all. Connected non-enforcing bootstrap may have
empty command scopes, never wildcard scopes. Enforcing bootstrap retains its
nonempty scope and production-admission requirements.

This explicit grant differs from the rejected alternative of showing every
installation to any authenticated principal. It is preferable for the shared
administrative service because installing the App no longer requires a second
catalog-ID change. The cost is intentionally broader metadata visibility to
admitted administrators. Reconsider this choice if tenants require independent
metadata confidentiality; use restricted grants until a separate tenant policy
is admitted. No repository mutation or execution authority is inferred.

The [design](../features/automatic-app-inventory.md) and
[implementation plan](../features/automatic-app-inventory-implementation-plan.md)
own pagination, isolation witnesses and rollout non-claims.
