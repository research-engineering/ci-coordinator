# Discover Installed Organizations

## Enable App-Wide Inventory

Configure the server with the existing organization-owned GitHub App ID and
private key, Keycloak authentication, and:

```dotenv
CI_COORDINATOR_CONTROL_PLANE_INVENTORY_MODE=app
CI_COORDINATOR_CONTROL_PLANE_INVENTORY_INSTALLATION_ALLOWLIST=
```

This is a one-time deployment setting. Each organization administrator must
still install the App and choose its accessible repositories in GitHub. The
Coordinator discovers those installations on refresh without a new numeric-ID
setting. An enterprise membership does not substitute for an App installation.

Restart only the owned Coordinator service after changing process settings.
Sign in with the admitted administrator identity, open Repositories, and
refresh the organization catalog. Organization and repository pages are
bounded; pagination is best-effort provider evidence, not a frozen inventory.

## Keep Commands Separate

For an administrator-only console managing every repository installed for this
App, also configure:

```dotenv
CI_COORDINATOR_CONTROL_PLANE_SCOPE_MODE=app
```

This requires App-wide inventory. After restarting the owned Coordinator
service, eligible repositories become available without individual numeric-ID
configuration. Every scoped operation checks the current App installation and
repository identity. A removed repository is denied; a provider failure returns
a temporary unavailable response, never a successful grant. Archived
repositories remain inspectable; repositories disabled by GitHub do not.

The default scope mode is `restricted`. In that mode, `Catalog only` means the
repository is visible but outside `CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST`.
The catalog exposes its exact scope and links here. To keep restricted access,
add the intended `INSTALLATION_ID:REPOSITORY_ID` pair to that setting instead.

Neither scope mode registers configuration, changes workflows, starts
observation, or authorizes selective CI automatically. Existing roles, CSRF,
configuration attestations and production-admission requirements still apply.
Break-glass operations always retain their explicit static allowlist, including
in App scope mode; workload roles are not promoted to administrator.

A non-enforcing App-inventory instance may start with the static allowlist empty.
In restricted scope mode every scoped command then remains denied. An enforcing instance still requires
its independent nonempty scope and production-admission evidence.

## Query Without The UI

Use an admitted Keycloak workload access token for the same read operation:

```http
GET /api/v1/workbench/installations?page=1&perPage=30
GET /api/v1/workbench/installations/INSTALLATION_ID/repositories?page=1&perPage=100
```

Read `hasNextPage`; do not infer continuation from item count. A failed page
does not establish an empty organization. Suspended and personal-account
installations are shown with their state but are not eligible for repository
adoption. Tokens and App private keys remain server-side.

## Retain A Restricted Deployment

The default `restricted` mode uses the existing explicit installation allowlist.
An empty restricted list performs no provider I/O. To revert App-wide visibility,
set inventory mode to `restricted`, scope mode to `restricted`, and configure
the intended IDs; command grants remain separate.
App mode plus a nonempty inventory allowlist is rejected as contradictory.
