# Cleanup Fixture Preparation Plan

Status: scoped source authored; native qualification pending

Design authority: [preparation design](cleanup-fixture-preparation.md).
Independent review follows current `AGENTS.md`; root owns publication,
requirement/witness registration and final integration.

1. Bind the clean worktree/base and inspect actual UoW, repository, claim,
   timeout, fixture and cleanup contracts. Record writer readiness before edits.
2. Add one helper to the existing cleanup-only support module. Use one real
   `PostgresCiEconomicsUnitOfWork` for register/claim/snapshot/report, preserve
   exact outcome assertions, and commit before returning to admin work.
3. Replace the four store calls in the capacity fixture with that helper. Keep
   population, budget configuration, expansion, full decode and epoch shifting
   unchanged; add a separately named committed seeding duration in nanoseconds.
4. Author native tests for one admitted seed and exact decoded source/evidence,
   report/origin/digest, signal/policy/retention relationships. Reject wrong
   snapshot/report identities after observing real writes, then prove all seed
   tables empty, connections returned, and an identical valid retry succeeds.
5. Run only scoped local Ruff, formatting, mypy and diff checks. Do not collect
   or run tests/benchmarks locally. Commit the four owned files once.
6. Root rebinds the new bytes and integrates the new test/doc bindings. Through
   the existing GitHub PostgreSQL route, run both tests in
   `backend/tests/integration/persistence/test_observation_cleanup_cost.py`.

## Native Acceptance

- The focused constructor witness observes one shared compatibility-fence
  acquisition per seed, real writes before both rejection variants, no partial
  state after rollback, and complete source/job/report/signal positive readback.
- The capacity witness retains 100 x 2000 expired jobs, 100 expired parents,
  reports and signals, one unexpired parent/job/report/signal, normal runtime
  triggers, the full decoded 2000-job sample and released connections.
- The unchanged captured selection and EXPLAIN identify 100 rows. Cleanup still
  executes 301 statements under the existing profile deadline, retains only the
  live child rows, produces 100 expired collection rows, and repeats/purges zero.
- Keep the original 180-second preparation limit. Report preparation,
  committed seed total and committed cleanup using monotonic nanoseconds.
- Bind observations to the actual GitHub head/run/job/runner. Compare compatible
  observations before claiming acceleration; the supplied earlier total alone
  does not identify the cause of cost or prove a speedup.

No production wiring, ACL, migration, provider or deployment change is needed.
Static success does not discharge any of the native acceptance items.

## Measured Expansion Follow-up

1. Preserve the prior source and native receipt. Measure seed, expansion
   construction, expansion transaction, retention shift and cleanup separately with
   the same monotonic clock; reject a cost claim from combined preparation.
2. Keep the existing canonical row generator, digest calculation and normal
   trigger mode. Replace only the 2,000-row SQLAlchemy fixture insertion with
   psycopg row-wise `COPY` on that transaction's raw driver connection.
3. Run the full-capacity native test before and after on one local Docker
   environment, and on the exact GitHub head. Compare all population and
   cleanup assertions, not just elapsed time. Retain the local and hosted
   results, including an unfavorable comparison.
4. Revalidate Ruff, mypy, documentation and exact-head Full Check. If `COPY`
   changes bytes, trigger effects, rollback or duration unfavorably, retain
   the original insertion and only the measured disposition.

## Prior Integration Handoff

The four-file batch adds `seed_cleanup_attempt`, changes the capacity test's
construction call site and timing field, and adds the two-case
`test_cleanup_seed_admits_once_and_rolls_back_partial_construction` witness.
At that stage the expansion and retention-shift helpers and cleanup operation
were unchanged; the measured follow-up above replaces only the expansion
write protocol. Root must register the new test/design/plan with the existing
requirement and native witness owners before closing qualification.

Supporting source read beyond the initial evidence projection includes
`persistence/compatibility_fence.py`, `persistence/connection.py`, the bundled
database compatibility and CI economics profiles, `ci_economics/model.py`,
`read_models.py`, `reports.py`, `budget_signal.py`, `budget_commands.py`, the
report/budget factories and `scripts/python_witness.py`. Root should include
these dependencies when rebinding the transaction/equivalence claims.

Local scoped Ruff and mypy checks do not exercise PostgreSQL. The typecheck
uses the repository's `--explicit-package-bases` convention. No local
behavioral execution, benchmark or measured speedup belongs to this handoff.
