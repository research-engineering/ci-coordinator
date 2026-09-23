# Historical Job Projection Implementation

Status: active implementation plan

Design owner: [historical job projection](history-job-projection.md).
Requirement owner: `REQ-CI-RUNTIME-046`. Independent batch review follows
the [repository policy](../../AGENTS.md).

## Ordered Work

1. Replace only archive job decoding with single-pass strict JSON admission,
   exact resource/state checks and existing Pydantic archive validation.
   Keep discarded display names outside admission; retain numeric identity.
2. Add raw provider boundary tests for partial runner metadata and every
   independent retained operand. Preserve strict decoder controls and feed
   the same payload through the real provider/application path in all lanes.
3. Clarify the requirement projection, register these two documents and refresh
   Proofkit route digests. Preserve every prior binding and published design.
4. Run local static types/imports/requirements/docs and mutation-anchor preflight;
   obtain one frozen independent review and native GitHub Full Check.
5. After exact-head qualification and squash merge, release with the already
   qualified run-source correction and observe an admitted pilot's original import progress.
   Keep old gaps and dataset authority intact. A healthy deploy is not proof of
   complete history, actual CPU accounting or production capacity.

## Acceptance Matrix

| Claim                                           | Independent native witness                                                                      |
|-------------------------------------------------|-------------------------------------------------------------------------------------------------|
| Partial display metadata does not block archive | Null/zero/positive IDs crossed with missing/different names; actual rerun-shaped payload        |
| Numeric identities stay strict                  | Bool, float, negative, unsafe, string and orphan group mutations                                |
| Resource binding stays strict                   | Isolated run, head and optional attempt substitution or invalid type                            |
| Terminal facts remain bounded                   | Unknown state/conclusion, active-null, missing required field, oversized page and invalid total |
| Labels remain canonical                         | Reordered equivalent input, duplicate/oversized/non-string/list-shape rejection                 |
| Uncertainty is not exactness                    | Reused earlier job times and reversed skipped-job times are preserved with inconsistent quality |
| Private data stays absent                       | Canonical bytes contain no runner/group names or step detail                                    |
| Strict evidence path is unchanged               | Same partial runner metadata still fails shared paired-identity admission                       |
| Composition reaches storage                     | Real adapter through backfill/recent/repair application lanes; exact write and request count    |

Negative numeric cases must reach raw JSON decoding without a canonicalizer
turning floats into integers or rejecting them before the subject boundary.
Cartesian parameter sets are finite tuples, not iterators: the pinned pytest
rejects iterator deprecation warnings under the repository warning policy.
Strict group-pair rejection is isolated with a valid named runner and a paired
positive control. Run-ID type rejection uses numerically matching boolean and
float inputs with integer positive controls, not only different identities.
New code, tests and plans are not runtime proof until their native run passes.
Failure in a late row must yield one deferred page, not a partial population.
