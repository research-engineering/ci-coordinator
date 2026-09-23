# History Time Authority Implementation

Design: [history time authority](history-time-authority.md).
Requirements: `REQ-CI-RUNTIME-046` and `REQ-CI-RUNTIME-047`.

## Ordered Work

1. Add required keyword-only `run_created_at` to the capability port. Forward
   the current admitted source value in the application's shared attempt reader
   for backfill, recent and repair. Preserve independent result admission.
2. Validate source time before provider I/O. Decode attempt creation separately
   from public run creation; compare private immutable header values on both
   sides of bounded job enumeration. Keep all identity, status, byte and page
   guards. Do not add HTTP calls or change deadlines.
3. Extend existing provider and application tests, using the actual adapter in
   one application-path witness. Update compact requirement/Proofkit projections
   and the document index. Published predecessor designs remain unchanged.
4. Run allowed static types/lint/import/contracts and the existing static
   mutation-anchor preflight. Obtain one independent review under `AGENTS.md`,
   then exact-candidate native GitHub qualification before squash merge.
5. Release/deploy the qualified image separately. Observe the original import,
   then use only the existing explicit non-destructive rescan if needed to
   revisit exhausted work. Do not manually edit cursors or clear statistics.

## Sensitive Oracles

| Predicate                  | Positive control                                 | Independent rejection                                         |
|----------------------------|--------------------------------------------------|---------------------------------------------------------------|
| Correct temporal owner     | Equal time, one-second difference, delayed rerun | Wrong returned run creation still rejected by application     |
| Stable attempt observation | Two equal attempt timestamps                     | Changing only the second attempt timestamp is unstable        |
| Exact source forwarding    | Backfill/recent/repair supply source time        | Fake adapter returning a different time cannot write          |
| Valid source input         | Aware UTC and equivalent offset                  | Naive/non-datetime rejected before provider calls             |
| Valid provider time        | Existing supported timestamp precision           | Invalid or missing attempt timestamp remains malformed        |
| Identity closure           | Actual provider-to-application success           | Existing scope/run/attempt/workflow/head substitutions reject |
| Cost and storage           | Existing request order/count; original run time  | No extra request, first-import reset or new authority         |

## Acceptance Boundaries

Native tests must exercise production application and adapter wiring, not only
a fake pre-normalized response. A green static contract check is not behavioral
proof. Deployment and observed recovery are separate receipts; neither source
tests nor an advancing cursor proves complete available history or CI savings.
Gap records remain historical observations, not assertions that recovery never
occurred. Active-evidence source resolution is a separately recorded follow-up.
