# Operator Evidence Presentation Implementation Plan

## Scope

Implement the bounded display contract in
[the design](operator-evidence-presentation.md). Preserve backend payloads,
UTC search windows, scoped command lifetimes and existing accessibility.
Historical design and plan documents remain unchanged.

## Ordered Work

1. Repair semantic formatting in `frontend/src/domain/format.ts` and its
   consumers: raw decimal IDs, shared UTC instants, singular count labels,
   display-only JSON formatting. Add parameterized formatter witnesses.
2. Repair identifier/table geometry in `IdentityDisclosure.tsx`,
   `DataTable.tsx` and existing evidence columns. Add exact-copy success,
   rejection and identity-change witnesses. Keep full-value inspection.
3. Apply existing CSS tokens consistently to section headings, action spacing,
   buttons, readable labels, disclosure markers, narrow summaries and tabs.
   Keep native selects, visible keyboard focus and horizontally scrollable
   semantic tables.
4. Correct catalog terminology, unavailable-action explanations, governance
   headings/prerequisites and operation-specific economics errors. Do not
   silently change command retry, authorization or admission behavior.
5. Update existing UI requirements, Proofkit bindings and roadmap routing.
   Run local static gates only; run behavioral and browser witnesses through
   the repository-owned GitHub workflow.
6. Freeze the candidate for the independent reviewer selected by `AGENTS.md`.
   Adjudicate findings against exact source. Merge only after exact-head gates;
   deploy only to the owned Coordinator service and inspect the real UI.

## Acceptance Witnesses

| Relation                                  | Required falsifier and evidence                                                                                                                                                                                                                               |
|-------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Identity is not a measured quantity       | IDs above 999 have no separators; counts and measurements retain grouping.                                                                                                                                                                                    |
| One instant, one displayed timezone       | Offset-equivalent inputs produce the same UTC string; invalid input remains visible. Governance reuses this formatter.                                                                                                                                        |
| Copy does not change identity             | Clipboard receives the complete original value; rejection remains readable and does not announce copied; late completion cannot label a new value copied.                                                                                                     |
| Display formatting does not mutate policy | Losslessly representable JSON is indented; non-JSON, unsafe numeric spelling or unsupported source-context input remains exact. The command path still uses original proposal source.                                                                         |
| Read versus write uncertainty             | Registration not-found, network and invalid responses have command-specific guidance and do not claim absent retained evidence. Exercise the console command path, not only its error component; retain exact-command browser retry.                          |
| Table geometry and navigation             | Desktop/mobile witnesses include long actor/hash, accessible disclosure, keyboard scroll and untruncated reason. Check page overflow separately from intended table overflow; neighboring short headings and event values must remain one rendered text line. |
| Consistent section inset                  | Report-budget headings have the same readable inset as other economics sections; inspect rendered text geometry in the retained-report journey.                                                                                                               |
| Presentation is not command authority     | Existing pending command, scope replacement, session expiry and idempotent retry witnesses remain required.                                                                                                                                                   |

Static type, lint, requirement/binding and documentation checks prove only
their respective contracts. Authenticated live evidence is distinct from
fixture-based browser results; neither establishes production capacity.
