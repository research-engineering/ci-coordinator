# Automatic App Inventory Implementation Plan

Status: implementation admitted after design review and plan control

## Contract And Ordering

The [design](automatic-app-inventory.md) owns visibility and authorization.
Implement the following steps in order. A later step cannot weaken an earlier
admission predicate. This plan shares a delivery batch with the independently
owned [CI efficiency plan](proof-preserving-ci-efficiency-implementation-plan.md).

## 1. Admit Inventory Policy And Page Values

Modify `provider_inventory/model.py`, `ports.py`, `permissions.py`, `service.py`
and their public exports. Represent an administrator's inventory grant explicitly:
restricted IDs or App-wide read visibility. An absent grant remains denial,
never a wildcard. Keep repository command scopes independent.

Add bounded installation page metadata and a provider page result. Default
queries to page 1 and 30 entries; limit page size to 100 and page number to the
existing provider page bound. Preserve unique canonical IDs per page. Distinguish
complete page, continuation and unavailable/partial result. Do not invent a
total installation count that GitHub does not return.

Native tests: denied actor causes zero provider calls; restricted empty is
deny-all; App mode admits a newly installed organization without an ID setting;
foreign, suspended and deleted installations cannot reach repository reads;
pagination and partial failures never become complete-population assertions.

## 2. Add The Provider Read

Modify `integrations/github/app_client.py`, `app_transport.py`,
`provider_inventory.py` and its existing decoding helpers. Admit only
`GET /app/installations` with the exact bounded page query and existing API
version. Reuse transport deadline, credentials, bulkhead, response-size and
pagination admission. No new HTTP stack or personal access token.

Use a fresh exact `get_installation` before browsing repositories. The service
must reject an identity mismatch before creating installation credentials or
fetching repositories. App and installation credential domains remain distinct.

Native adapter/transport tests cover route/query substitution, foreign `Link`,
duplicates, malformed arrays, suspension, rate limits and provider failure.
Test multi-page and exactly-full terminal pages; do not infer continuation
from row count alone.

## 3. Wire Settings And HTTP

Modify `runtime_settings/admission.py`, `contracts.py`, `redaction.py` and
the existing environment inventory; wire `runtime/composition.py`. Add closed
mode `restricted` (default) or `app`; reject App mode plus a nonempty inventory
allowlist. Permit missing/empty command scopes only for connected non-enforcing
App-mode inventory bootstrap. Keep restricted and enforcing requirements.
Audit every composition/authorizer use of that empty set for strict deny-all
behavior; preserve existing command grants independently of inventory IDs.
In particular, `operator_controls/auth.py` and
`app/ci_measurement_report_ingestion.py` must construct with an empty frozen
scope set and deny every operation. Scope membership remains mandatory before
provider/storage I/O; no alternate wildcard or conditional service is needed.
Retain nonempty positive cases and add empty-set denials at both owners.

Modify the provider-inventory HTTP router and response projections. Add bounded
page queries without changing the administrator authentication boundary. Update
test doubles and generated OpenAPI/browser contracts through their generators.
Update both endpoint response sets and the separate rate-limited-operation
projection; these independently bind the admitted error schema.
Use App mode only for the owned fresh development profile; do not rewrite
existing local state or live deployment configuration implicitly.

Native tests cover old configuration, empty App-mode bootstrap with every
command denied, restricted/enforcing empty rejection, invalid mode, contradictory settings,
redaction, query boundaries, and login/authorization before provider I/O.
The runtime catalog success case uses an administrator with the `read` role;
the separate break-glass case must return 403 before any installation lookup.
Emergency credentials are never promoted to administrator inventory authority.

## 4. Complete The Browser Journey

Modify `frontend/src/api/providerInventory/{client,schema}.ts`, the development
proxy query admission, `features/workbench/useProviderInventory.ts` and
`RepositoryCatalog.tsx`. Add organization-page navigation and refresh without
an unbounded all-pages browser cache. Bind delayed responses to page, selected
installation and session/request lifetime; reset repository state on changes.

Retain the sidebar, readable full names, native select affordance and explicit
found-but-not-command-authorized state. A disappeared selection cannot remain
an actionable stale scope. Add browser/unit tests for second organization,
pagination, empty/partial/error results, selection changes and session loss.

## 5. Publish Governing Contracts And Evidence

Update the provider-inventory module contract, UI requirements and Proofkit
routes, `docs/INDEX.md`, `ROADMAP.md` and runtime configuration how-to. Keep
pre-existing feature designs/plans unchanged. Route every new implementation
and test path; generated route metadata is not a substitute for native tests.

Run locally only admitted lint, type, import, static schema and documentation
checks, then freeze implementation bytes for independent review. Repair
confirmed findings, publish the branch and PR, then run HTTP/provider,
settings, frontend and browser witnesses in exact-head GitHub Full Check.

## Acceptance And Rollout

Require the new exact-head Full Check, unchanged command-denial witnesses and
the new pagination/visibility witnesses before merge, not before PR creation. Do not
claim deployed visibility from source tests. A later owned-service rollout
enables App mode once and checks administrator login plus a second real
installation. pilot target workflow mutation is not part of this plan.

Rollback is the previous binary/configuration, or explicit restricted mode
with its old allowlist. There is no database migration or durable catalog to
repair. Restoring restricted mode intentionally reduces visibility but never
revokes or grants command authority by inference.
