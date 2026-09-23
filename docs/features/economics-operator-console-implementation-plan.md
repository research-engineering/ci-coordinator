# Economics Operator Console Implementation Plan

Status: implementation; native qualification pending

The [design](economics-operator-console.md) owns the behavioral delta,
invariants, scope and alternatives. Existing measurement and planning
contracts remain unchanged.

## Delivery Order

1. Add source-page and report-pointer contracts under `ci_economics`; extend
   existing read ports. Bind their finite order, cursor, identity and retention
   semantics to focused parameterized witnesses.
2. Extend PostgreSQL read adapters with indexed bounded projections. Source
   reads decode the existing authoritative collection state before projecting
   public fields. Report lists use one scoped outer join without payload bytes.
3. Extend existing application read services, preserving authorization before
   I/O and rejecting cross-request responses. Expose both versioned GET routes
   through the existing audit role, error algebra and request admission.
4. Add requirement and Proofkit routes, regenerate OpenAPI operation types and
   update exact route/proxy/model inventories. Do not hand-edit generated API
   types or weaken alias-only request admission.
5. Implement capability-local runtime schemas and clients for catalogs,
   discovery, registration, measurements, reports, comparison and budget reads.
   Reuse bounded fetch, same-origin authentication and in-memory CSRF.
6. Build the repository economics tabs and bounded run/report selection flows.
   Preserve the existing reconciled-attempt view. Render server decisions with
   explicit units, provenance and uncertainty; no browser policy duplication.
7. Add native component and browser witnesses for complete journeys, stale
   responses, failed commands and narrow layouts. Update the operator how-to
   and API reference without copying the design's canonical predicates.
8. Freeze the coherent source delta for the reviewer selected by `AGENTS.md`.
   Run exact-head GitHub gates, resolve confirmed findings, squash and inspect
   post-merge evidence. Release and live deployment have separate admission.

## Witness Matrix

| Boundary           | Dangerous counterexample                                                         | Native evidence                                            |
|--------------------|----------------------------------------------------------------------------------|------------------------------------------------------------|
| Domain pages       | Wrong scope, kind, digest, duplicate/order or cursor contradiction               | Isolated parameterized contract cases                      |
| Source retention   | Expiry equality or tombstone leaked into current catalog                         | PostgreSQL boundary cases using DB-owned time              |
| Report query       | Source absent confused with empty; expired/foreign report visible                | PostgreSQL outer-join, scope and retention cases           |
| Resource bound     | Full payload list, unbounded rows, OFFSET or provider reads                      | SQL shape/row-bound witnesses and exact adapter tests      |
| Application        | Unauthorized query or schema-valid foreign result accepted                       | No-call authorizer and independent binding mutants         |
| HTTP               | Duplicate/malformed query, route admission mismatch, internal lease data exposed | API and exact OpenAPI/request-admission checks             |
| Client             | Oversized or cross-scope response accepted                                       | Bounded runtime schema/transport cases                     |
| State              | Old authority/request result overwrites current selection                        | Controlled completion/cancellation hook cases              |
| Commands           | Navigation registers; retry changes attempt; conflict appears successful         | Component and native browser command witnesses             |
| Presentation       | Unknown becomes zero; CPU conflated with occupancy                               | Metric-quality and comparison rendering cases              |
| Responsive journey | Mobile action loses target identity or inactive tab retains focus                | Desktop/touch/narrow Chromium screenshots and interactions |

## Owner And File Boundaries

`ci_economics/catalog.py` owns page value semantics, not HTTP or SQL.
Existing `ports.py` declares reads; existing `app/ci_economics.py` and
`app/ci_measurement_reports.py` sequence authorized reads. Existing persistence
repositories and their transactional adapter own database effects. Narrow
HTTP catalog models project wire shape; existing routers own transport.
New frontend catalog, measurement and command modules stay within the
economics capability and reuse the existing workbench/session composition.

No new database schema or production service is planned. Every new file must
have a concrete consumer, a scoped owner and a routed witness. Whole-chain
acceptance includes type/format/import/ownership/documentation/Proofkit gates,
native PostgreSQL and API tests, frontend coverage/mutation and browser
journeys. Tests execute only in GitHub; static projections do not prove them.

## Non-Claims

The first native run exposed three test-integration gaps: colliding HTTP/app
pytest module names, a pre-console App default-reader fixture, and the HTTP
model inventory missing four new response models. Their repairs preserve
test bodies, lazy GET-only navigation, exact model policy and mutation gates.
Additional witnesses reject an old-scope browser response after navigation, retry an
unchanged registration after failure, race two reads within one hook, and
evaluate the adapters' actual retention predicates around one PostgreSQL
statement clock. The last witness substitutes only the expiry operand; it
does not replace the production comparator or claim full query behavior.
Existing persisted catalog scenarios cover the remaining query relation.
The control review must bind all native witness files as evidence owners,
not merely include their paths in the target inventory.

Native qualification also identified an unregistered domain-backed
`AfterValidator`, the two new requirements missing from a witness count, and
browser fixtures that ignored normalized datetime inputs, nested metric scope
text and collapsed navigation. Repairs preserve the underlying policies.
The stale-response proof is deliberately split: a component awaits old-read
settlement inside React `act` after the new read completes, while Chromium
observes terminal rejection of a valid old-repository page and successful
explicit retry for the new repository. This avoids assuming that animation
frames imply completion of asynchronous response admission. Neither layer is
claimed to prove the other's boundary, and no product test hook is introduced.

This batch does not complete cohort statistics, persistent alert subscriptions,
automated PR publication, organization-wide savings, a pilot, production
admission or the optional UI chat. Those remain in the global roadmap.
