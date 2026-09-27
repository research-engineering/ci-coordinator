# Operator Outcome Truthfulness

Status: admitted implementation; native qualification pending.
Date: 2026-09-27. Backlog owner: CI-054.

## Decision And Owners

This refines the existing operator UI requirements U001, U003, U005, U007,
U016 and U021, not review V2 or backend authorization policy. The affected
owners remain separate; sharing frontend qualification does not merge their
business authority.

| Owner | Minimal delta | Protected observations |
|---|---|---|
| ArchiveRetention and ArchiveGapRepair | A current initial typed unauthenticated/forbidden response is rejected and closable. | A previous unknown operation remains unknown with the exact original command; ready receipts, abort fences, hidden lifetime and explicit retry remain. |
| RepositoryCatalog | An active catalog awaiting selection normalization or repository admission is pending, not empty or failed. | Existing selection effect, page/search, request keys, scope, stale-response rejection and lifetime. |
| ArchiveRetention and ArchiveGaps | Selected non-null instant cells use the existing UTC formatter and retain the original dateTime attribute. | Null labels, stored precision, payloads, commands, revisions and retention policy. |
| Workbench payload admission | A numeric leaf must be finite with magnitude at most 9007199254740991. | Integer identifiers/revisions and all depth, node, collection, text, key and response bounds. |
| API inventory documentation | List the three capabilities already owned by the module profile. | No new capability, policy, waiver or generated authority. |

The refusal predicate uses the phase captured before sending. An initial
denial says the target business mutation was not admitted; it does not deny
authentication or security-audit effects. A later denial cannot resolve a
previous lost reply. Arbitrary 4xx, malformed bodies and transport failures
remain uncertain. An obsolete lifetime publishes neither denial nor success.

Keeping the catalog effect is sufficient: presentation uses active membership,
the current selection and idle/loading/settled read state. An eager fallback
selection could request a new installation using an old repository page.
No shared mutation hook, cache, parser, router or framework is introduced.

## Plan And Independent Acceptance

1. Bind the existing owner operands and preserve current V2 review/renewal.
2. Author the five local frontend deltas and the three inventory lines.
3. Qualify each row independently using existing test owners:
   first typed denial versus unknown retry; actual intermediate catalog commits,
   retained/replaced selections and late responses; actual UTC/null cells;
   finite payload positives and adjacent integer/resource negatives.
4. Bind one synthetic complete response vector to the backend audit
   construction, projection and real ASGI response, then to the frontend client.
   The vector is compact response bytes followed by exactly one LF. It is not
   a provider capture. A separate PostgreSQL witness uses sanctioned append,
   commit, exact replay and scoped workbench read, without a fixture SQL bypass.
5. Root integrates only missing proof relations and qualifies final bytes in
   GitHub; independent review precedes publication.

Required native gates include frontend quality/browser, Python unit/persistence,
contract and documentation/ownership/proof admission. Source-authored tests,
static checks and independent fixture hashing are not native passes, paint or
assistive-technology evidence. There is no claim of a deployed fractional-event
incident, measured performance gain or complete audit closure.

A new production owner, a changed lifetime, unsupported wire identity or loss
of V2 semantics reopens admission. All other FE conditional claims and evidence
gaps remain in their existing audit disposition; no new backlog is created.
