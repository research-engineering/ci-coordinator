# Archive Analytics Product Plan

Status: source and scoped witnesses authored; integration and native qualification pending

Contract: [archive analytics product](archive-analytics-product.md). This plan
owns execution and evidence status, not a second metric definition.

1. Freeze base `4404947b99db9d767fbee587e626b4dd5fd8c62e` and writer paths.
   Done: clean `feature/archive-analytics` worktree; existing owners read.
2. Author the minimum sufficient model and writer-readiness before code.
   Done: product contract specifies operands, exclusions, costs and falsifiers.
3. Add bounded domain contracts, aggregation, chronological forecast and
   duration state machine. Add scope-authorized application orchestration,
   existing-UoW scalar query adapter and isolated HTTP router/contracts.
   Done: SQL returns daily buckets, not client-side attempt/job history;
   descriptive slowdown remains available without workflow blob provenance.
4. Author scoped native tests for independent domain operands, authorization,
   query/result binding, real PostgreSQL selection/timing, limits and HTTP errors.
   Run only permitted static Ruff/mypy checks locally.
   Done: native cases authored; no test execution or collection in this lane.
5. Commit the cohesive owned delta once. Return exact SHA and integration needs.
6. Root integrates router dependencies/composition, mapping configuration,
   OpenAPI/client generation, UI, unique requirements, Proofkit bindings/routes
   and documentation routing. Root runs exact-head
   native tests, boundary/contract gates and independent review under AGENTS.md.
   This lane does not execute tests, dispatch CI, publish or deploy.

## Native Acceptance Ledger

| Predicate                                                           | Intended witness                                       | Qualification            |
|---------------------------------------------------------------------|--------------------------------------------------------|--------------------------|
| Unknown is not zero; partial/conflict inputs cannot enable forecast | Domain sparse/partial/conflict cases                   | Pending root execution   |
| Run IDs deduplicate; attempts and selected job totals do not        | Aggregation/filter cases                               | Pending root execution   |
| Future holdouts cannot affect earlier predictions/bands             | Prefix mutation and chronological fold assertions      | Pending root execution   |
| Sustained slowdown, middle band and recovery are distinct           | Step/ramp/non-regression/unknown cases                 | Pending root execution   |
| SQL scopes and generation agree with request and dataset            | Native PostgreSQL fixture                              | Pending root execution   |
| Query budgets do not silently truncate                              | Attempt/job and response cap cases                     | Pending root execution   |
| Authentication and repository authorization precede archive reads   | Service and route denied cases                         | Pending root execution   |
| Wire projection remains typed and safe                              | Route/OpenAPI/type checks                              | Pending root integration |
| Available/unavailable variants cannot contradict their fields       | Forecast support/band and HTTP exclusive-outcome cases | Pending root execution   |

No native pass, exhaustive oracle audit, production calibration, deployment or
complete roadmap delivery is claimed. Source mutation starts a new target epoch;
root must rebind it before independent qualification.

## Static Checks And Processing Observations

The final local static set is all 17 newly added Python files (nine source,
eight test/support files). Ruff 0.16.5 `check --no-cache` and `format --check`
and mypy 2.3.1 `--cache-dir=/dev/null` were run against this explicit set. Mypy
used `PYTHONDONTWRITEBYTECODE=1` and `python -B` in the existing recovery static
environment. This does not prove a fresh locked installation or native behavior.
The sole local type suppression is the vendor's untyped PostgreSQL dialect
constructor in the SQL-compilation test; domain and result checks remain strict.

Observed processing errors, retained without inventing host identifiers:

- Shell chunks `2fa965` and `f91860`: zsh rejected unmatched exploratory globs;
  exact-path/parent-directory reads replaced them. No source write occurred.
- Shell chunk `397ab6`: neither probed `backend/.venv` existed; static tools were
  resolved from existing local caches instead of installing dependencies.
- Patch admission rejected one duplicate-path envelope and one stale context;
  both were corrected after checking current bytes. The host returned no
  session or occurrence ID for these patch failures.
- Mypy session `91964`, chunk `702f06`: two source annotation errors corrected.
  Session `98916`, chunk `ce43a0`: fixture keyword typing and the vendor dialect
  constructor were untyped. Session `72358`, chunk `9913aa`: the remaining two
  calls shared that same vendor constructor boundary, now explicitly isolated.
- Ruff chunks `eb4d67` and `b13209`: formatting/import ordering and an explicit
  `zip(strict=False)` were required and corrected.
- Mypy session `67865`, chunk `060d5b`, and Ruff chunks `a58bb5`/`182115` passed
  their then-current static set. After the final algebra changes, mypy session
  `72345`, chunk `ef722f`, passed all 17 Python files; Ruff check chunk `0fa098`
  and format-check chunk `e7d268` also passed. No native execution is implied.

For non-session tool failures, a task-session/occurrence ID was not exposed by
the host; chunk identifiers above are command-output coordinates, not invented
execution-session or native qualification receipts. Root retains re-admission,
independent oracle adjudication, native PostgreSQL cost and whole-chain gates.
