# Archive Operator Workspace

Status: implementation design; native and live qualification pending

## Decision

The archive and analytics become one operator journey: select a repository,
inspect retained history, compare measured usage, and follow a signal to its
contributing attempts. Existing economics, identity and archive owners retain
their policy; the UI owns navigation, request lifecycle and presentation only.
The [implementation plan](archive-operator-workspace-plan.md) owns delivery order.

Current economics exposes operational tables and settings but not a historical
analytics view. Current account controls occupy the page header. The intended
delta adds a useful overview and historical drill-down, keeps settings separate
from results, and places account access in the persistent sidebar. It does not
create another metrics subsystem or authorize provider effects.

## Protected Relations

For a response R and current view V:

```text
Render(R, V) => ValidSchema(R) and ExactScope(R, V)
               and CurrentAuthorityEpoch(R, V)
               and RequestedFilters(R, V)

UnknownMeasurement != 0
ObservedRunnerTime != CPUTime != FinancialSavings
ObservedSlowdown != ProvenCodeRegression
Forecast != Observation
```

Use the existing bounded transport and session recovery. Scope, generation,
authority or filter changes abort old reads and remove foreign results. A
background refresh may retain an explicitly stale same-scope result, but it
cannot relabel that result current or replay an administrator mutation.

The existing scope/authority-owned retained task also encloses economics across
primary-sidebar navigation. Hidden History and Observation preserve their
command state but stop status polling and archive reads; other inactive panels
unmount. Returning never automatically repeats a POST. A changed scope or
authority still destroys obsolete state. No browser persistence or global store
is needed for this navigation guarantee.

History chooses its initial subview from the first admitted status: settings
when unconfigured, records otherwise. That choice persists when a later refresh
discovers a configuration committed behind a lost response. Status refresh must
not hide the editor's uncertain operation or silently change the selected task.

The server owns archive population, retention, forecast eligibility, category
mapping and compatibility. The browser must not reproduce those decisions.
Missing measurements, incomplete populations, suppressed forecasts and query
limits remain visible; a source404 is not rendered as proven deletion.

The forecast plot compares disjoint horizon-sized historical periods with one
aggregate future period. It does not distribute a joint horizon interval across
individual future days. Missing historical operands remain gaps, and a future
period has no observed value. Native browser and component witnesses must retain
that distinction. Archive and analytics modules are loaded on demand; the
unrelated initial operator view does not eagerly load their presentation code.

Development proxy admission owns only finite method/path/parameter forwarding.
It does not import the browser response schemas or replace server admission.
That smaller boundary avoids making browser-schema edits restart the native
Vite configuration and preserves the existing disjoint HMR/restart closure.
Only its small transport module uses native `.ts` imports. A native import
witness remains necessary; a bundler-only pass is insufficient.

## Presentation

- Analytics is a bounded page with period and workflow/job filters, concise
  measured totals, a usage series and links to retained attempts. Unit, sample
  support and coverage are adjacent to each result, not hidden in a disclaimer.
- Source inspection preserves the exact repository, generation, workflow/job
  filters, UTC interval and configuration/data revisions. A slowdown opens its
  daily source window. Changed revisions refuse to present a different archive
  as the chart's evidence; returning restores the filter selection and refreshes
  analytics. Source inspection is read-only, with no retention mutation controls.
- Measured and selected job counts accompany daily values and forecast-period
  history. Missing duration, inconsistent timing and excluded conflicts remain
  separate. A complete retained attempt population does not establish complete
  timing samples. Forecast periods have no fabricated observation support.
- Historical records use keyset pagination. Jobs and available details are
  loaded for an explicit selected attempt. Settings and destructive previews
  are separate from the result table; preview never performs the mutation.
- A chart uses a fixed SVG coordinate space and a semantic table alternative.
  Unknown points break a line rather than connecting across missing evidence.
  Each scrollable table viewport is named and keyboard-focusable, including
  read-only job/gap tables without an interactive descendant.
  Observations and forecasts use distinct styles and an explicit cutoff;
  intervals are uncertainty bands, not another observation series.
- Charts remain read-only projections. Native SVG is sufficient for bounded
  series, with no new plotting dependency, raw provider HTML/SVG or editable
  workflow designer. Reconsider a plotting library only after a measured
  interaction/layout need exceeds this bounded presentation contract.
- Account access remains keyboard reachable in expanded, compact and mobile
  navigation. Session errors and logout are never concealed by the compact
  layout. A small account disclosure is preferable to a duplicated auth hook.

## Writer Readiness And Acceptance

Shared integration extends the existing HTTP dependency registry and composition
root with archive reads and analytics, reusing the current authorizer and history
unit of work. Retention event registration is pair-owned: ordinary generic
append cannot manufacture a committed retention receipt. No database schema
change is needed for these existing tables and grants.

Archive cursors use a 32-byte HKDF-SHA256 subkey derived at composition from the
existing Ed25519 plan signing key, under a separate versioned info string and
fixed salt. The private key is decoded to canonical raw bytes, so PEM formatting
cannot break replica consistency. Cursors never authorize access or expose the
key. Rotation invalidates outstanding cursors; the UI restarts pagination.
This avoids a new operational secret and preserves API-only deployments without
  enabling browser identity. Revisit if cursor and signing-key rotation lifecycles
must be independently governed. Use the existing cryptography library and
require stable same-key output, rotation separation and wrong key-kind rejection.

| Owner           | Intended delta                                                 | Protected observations                                    | Independent falsifiers                                                    |
|-----------------|----------------------------------------------------------------|-----------------------------------------------------------|---------------------------------------------------------------------------|
| API admission   | Bind new response shapes and requested scope/filter/generation | Existing schema owner, safe error algebra, request limits | Wrong repository, extra fields, stale generation, malformed date/count    |
| Query lifecycle | Fetch only active views, cancel stale work                     | Session recovery, no mutation replay, bounded fetch       | Late old response after scope/filter/authority change                     |
| Chart           | Map admitted finite points to fixed coordinates                | Metric meaning, nulls, source labels and units            | Gaps, all-null/all-zero, extreme finite values, long labels, empty series |
| Navigation      | Persistent sidebar account and linked analytics/history        | Native keyboard behavior, current scope, logout           | Compact/mobile account failure, focus loss, inaccessible action           |

Root owns these frontend changes and shared integration; backend writers do not
edit these files. The cheapest sufficient whole-chain gate combines schema and
component witnesses with GitHub browser screenshots/accessibility and exact-head
backend tests. Static checks alone do not establish rendering or real Swarm
login. No design is claimed globally optimal; revise it if it obscures the
operator's next action or duplicates a server-owned policy.

Browser wire admission verifies Unicode scalar bounds, exact mapping scope and
generation, unique mapping keys/purposes and its canonical digest. Forecast
holdouts must be ordered, contiguous, horizon-sized and cutoff-bounded at
microsecond precision, with phase-specific intervals and paired diagnostics.
These checks reject contradictory transport data; they do not run or replace
the forecasting algorithm. Native false-input tests retain an admitted positive
control before mutating each independent operand.

Archive transport admits at most 2 MiB: 50 jobs at the existing 32 KiB per-job
bound plus a 64 KiB parent/query/cursor envelope fit below that limit. JSON
escaping counts toward the bound, including control characters in admitted
labels. Analytics keeps its separate 1 MiB budget. Positive full-page and
oversized-response witnesses prevent treating a legitimate page as malformed
or removing the response cap altogether.
