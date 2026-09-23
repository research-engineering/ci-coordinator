# Recorded History Gap Recovery Plan

The [design](history-gap-recovery.md) owns semantics and alternatives. Base:
`dfdf31ef4a28393808668aceb8dba895827d43bb`. One vertical batch covers the bounded
operator recovery journey; unrelated throughput changes remain measurement-led.

## Implementation

1. Add capability-owned strict request, server-enriched command, admitted
   interval/receipt and rejection models in `ci_economics/history_gap_recovery.py`.
   Reuse canonical hashing, scope values and Pydantic boundary conventions.
2. Add `persistence/ci_history_gap_recovery.py`: current scope/dataset lock,
   bounded canonical gap selection, interval admission, existing queue merge,
   exact paired audit and historical replay. Expose it through the existing
   history store and administration service; do not add a table or new lifecycle.
3. Extend the bounded history read projection with current matching headers and
   retry-operand support. Keep one statement snapshot and existing cursor rules.
4. Add the strict authenticated HTTP command to historical administration,
   sharing its write admission and deadline policy. Register the body rule,
   generated OpenAPI projection, client contract and Activity event projection.
5. Extract the existing gap table into its capability component and add selected
   page recovery with existing command/session hooks. Preserve unresolved-command
   identity, native controls, bounded scrolling and responsive accessibility.
6. Register the new runtime requirement and affected read/UI routes through the
   current Proofkit integration. Update routing/overviews, not old design files.

## Independent Acceptance Matrix

| Owner       | Positive control                                                        | Independent falsifier                                                                                                             |
|-------------|-------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| Command     | Valid sorted multi-gap request                                          | Duplicate IDs, actor injection, item/span/byte bounds                                                                             |
| Identity    | Multiple gap records for a run                                          | Foreign scope/generation, conflicting metadata, absent/invalid canonical record                                                   |
| Queue       | Multi-run enqueue then normal collection                                | Late capacity failure rolls back earlier enqueue and audit                                                                        |
| Replay      | Same command after queue consumption or an erased dataset postcondition | Changed actor/targets conflict; valid audit bytes with false source/interval relations fail; replay cannot recreate consumed work |
| Concurrency | Concurrent identical commands                                           | Exactly one admission/audit; stale worker CAS after an admitted rewind fails                                                      |
| Dataset     | Active selected workflow                                                | Pause/erase/configuration/selector changes fence new requests                                                                     |
| Projection  | Missing, partial, complete, conflicting headers                         | Wrong run/attempt/generation and malformed rows cannot become another gap's evidence                                              |
| HTTP        | Fresh authorized administrator                                          | False/unknown authorization, missing CSRF, duplicate JSON, bad media, bounded overload                                            |
| UI          | Page selection, queue receipt, refresh                                  | Scope/session replacement, unknown response and stale completion cannot report success for another target                         |

The PostgreSQL witness must use real transactional adapters and verify later
collector import while preserving original gap and statistical accounting.
Tests asserting only model shape or helper invocation do not close that path.
An administratively prepared erased postcondition qualifies only historical
replay with absent source rows; it does not implement or qualify a public
statistical-erasure lifecycle.

## Qualification And Delivery

Run only permitted local static/type/contract checks and exact-range Proofkit
routing. Use one independent reviewer under `AGENTS.md` for the complete frozen
candidate alongside native GitHub qualification. Group confirmed repairs;
never lower gates, rewrite published PR history or run local behavioral tests.

After exact-head acceptance, squash-merge and qualify the exact master/release.
Deploy through the existing admitted development path with unchanged schema,
permissions and consumer workflows. In the real administrator browser inspect gap labels,
submit an authorized bounded retry and distinguish queued, imported, partial
and still-unavailable outcomes. Compare original and successor retained facts;
neither a smaller error counter nor a successful enqueue proves completeness.

Save original provider/census observations and final results outside the
repository. Update the existing global roadmap projection and Proofkit feedback.
Retain every unresolved workload, provider and operational qualification item.
