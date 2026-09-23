# Administrator Repository Access

Status: implementation contract

Owner: `operator_controls` scope admission; `provider_inventory` provider facts

## Decision

Administrators must be able to open and configure repositories installed for
the organization-owned GitHub App without adding each repository ID to the
deployment. Add the explicit process setting
`CI_COORDINATOR_CONTROL_PLANE_SCOPE_MODE=app`. Its default is `restricted`.
App scope mode requires App inventory mode. Existing deployments do not gain
authority merely by upgrading or refreshing their catalog.

This changes administrative scope admission, not repository registration,
observation subscriptions, workflow execution or omission authority. The
[implementation plan](administrator-repository-access-implementation-plan.md)
owns delivery order. This contract supersedes only the static-only scope
assumption of [automatic inventory](automatic-app-inventory.md); its inventory
pagination and credential boundaries remain unchanged.

## Protected Behavior And Intended Delta

Let `A` be the freshly authenticated administrative actor, `R` its admitted
operation roles, `S=(installationId,repositoryId)`, `L` the static scope set,
`M` the explicit scope mode, and `P(S)` fresh provider membership evidence.

```text
AdministrativeScope(A,S) = Administrator(A) and
  ((M = restricted and S in L) or (M = app and P(S)))

BreakGlassScope(A,action,S) = BreakGlass(A) and S in L and
  action in {force_full_ci, disable_omission}

OperationAllowed = FreshIdentity and RequiredRoles subset R and
  AdministrativeScope and ExistingOperationAdmission

AdministrativeScope does not imply RegisteredConfiguration
AdministrativeScope does not imply ProductionAdmission
AdministrativeScope does not imply ObservationEnabled
```

In App scope mode, even a statically listed administrative scope requires
provider membership. It is not a fallback around revoked access. Break-glass
retains its independent static emergency grant and never performs GitHub I/O.
Workload roles remain least-privilege; browser administrators receive their
effective roles from Keycloak, not from client-side role promotion.

## Membership Evidence

Reuse the existing strict provider-inventory adapter:

1. Read the exact installation ID with the App credential and require an
   active organization installation. This prevents a foreign App installation
   from granting access and admits its immutable account ID.
2. Read installation-authenticated repository pages until the requested numeric
   ID is found. Every row in that page must match the installation account.
   Admit only the exact scope with `disabled = false`; names grant no authority.

The [GitHub installation repository list](https://docs.github.com/en/rest/apps/installations#list-repositories-accessible-to-the-app-installation)
owns this membership evidence. Public metadata or a name-based installation
lookup is insufficient: a rename/name-reuse race can bind one repository ID
to another repository's installation. A positive page is sufficient; exhaustive
negative inventory and an atomic provider snapshot are not claimed.

The adapter admits exact method, operation, path, canonical page query, empty
body, API version, successful status and continuation evidence. Stop at the
first exact match or terminal page, at 100 pages of at most 100 rows, or at one
10-second monotonic deadline covering all pages and credential acquisition.
Exceeding either bound is unavailable, not denial or a partial positive grant.
This bounds worst-case requests and retains at most one decoded page. Existing
transport bounds and shared concurrency admission also apply.
No positive membership cache or durable grant is created. A returned Boolean
is valid for this operation's admission only, not an irrevocable provider lock.
Revocation after the last provider observation can race a local operation;
there is no cross-system atomicity claim.

```text
installation.id != S.installationId               -> unavailable
installation.accountId != page.repository.ownerId -> unavailable
installation inactive or non-organization         -> denied
repository disabled                               -> denied
provider not_found                                -> denied
timeout, rate limit, malformed or unknown evidence -> unavailable
```

Archived repositories remain inspectable. Workflow/configuration eligibility
is decided by existing operation owners. Cancellation propagates. Unknown
membership raises a typed scope-admission unavailable outcome, mapped to a
redacted non-cacheable HTTP 503; it must not become 403, empty success or 500.

## Catalog And Administrator UX

The catalog already has App-authenticated active installation evidence and
installation-authenticated repository-page evidence. In App scope mode it
projects eligible page rows as available without one extra provider request
per row. Actual operations independently recheck membership and identity.

The Open action remains available for those rows. Restricted deployments
retain their explicit scope policy and show its exact configuration remedy,
not an instruction for an administrator to ask another administrator. A
disabled GitHub repository remains visibly unavailable with its actual reason.
No UI change fabricates a configured, observing, optimized or ready state.

## Ownership And Simpler Alternatives

The GitHub import policy admits only the capability-owned unavailable exception,
not the scope authorizer or override policy. The adapter reports missing evidence;
the scope owner still decides actor/action authority. Negative boundary tests
prevent this narrow dependency from becoming a general domain-policy import.

| Candidate                                                   | Decision and cost                                                                                                                             |
|-------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| Remove the UI disabled attribute only                       | Rejected: the server still denies the operation; no working configuration path.                                                               |
| Reuse App inventory mode as command authority               | Rejected: upgrading silently broadens existing visibility-only deployments.                                                                   |
| Durable editable per-repository ACL                         | Deferred: introduces schema, recovery and UI lifecycle for a grant already owned by GitHub App installation and the deployment administrator. |
| Numeric metadata followed by name-based installation lookup | Rejected: rename/name reuse can join two different repositories, and public metadata alone is not membership.                                 |
| Fresh repository-specific token per operation               | Deferred: adds token-issuance policy and credential lifecycle for a read already supported by inventory.                                      |
| Bounded installation repository pages                       | Selected: positive exact-ID evidence, existing adapter, no name join or new state; usually one page, at most 100 under one deadline.          |

The scope authorizer owns the policy and its small membership port. The
GitHub adapter owns membership observation. Runtime settings own opt-in;
composition supplies dependencies. Inventory projects the same mode over
its independently admitted pages. HTTP owns status and redaction. There is
no new database table, background worker, generic policy engine or UI role
editor. These boundaries are sufficient within this declared candidate set,
not a proof of universal optimality.

## Required Falsifiers

The restricted-mode remedy links to project documentation. A real repository
name is not sufficient evidence of synthetic data: the documentation URL is a
counterexample. Production asset admission therefore retains development-module
provenance and synthetic/identity markers, but no longer bans the real project
name as a substring. A native build must admit the documentation link while
still rejecting aliased development modules and copied synthetic assets. This
changes neither permission admission nor the requirement to exclude demo code.

Native tests must independently challenge principal type, role/expiry/CSRF,
scope mode, static membership, provider repository ID, installation ID, owner
ID, active state, public-but-unselected repository, disabled and archived
flags, request provenance, invalid pagination, malformed response,
unavailability and cancellation. Restricted mode and emergency controls must
remain operational without provider I/O. Catalog projection must not make
N additional provider calls for N repositories. Runtime composition must
wire the same mode into catalog and operation authorizers.

Native CI and real Swarm login are separate acceptance evidence. This feature
does not prove production readiness, complete repository observation or
CPU savings. Revisit if GitHub changes the membership endpoint contract,
measured admission latency exceeds the request budget, multiple administrative
tenants need different scopes, or delayed revocation is unacceptable. Do not
add a cache before its revocation and freshness contract is admitted.
