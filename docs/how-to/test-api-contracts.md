# Test API Contracts

Schemathesis tests the actual in-process HTTP composition with synthetic
identities and capability ports. It never targets a deployed coordinator,
GitHub, Keycloak or a database. It is a development dependency, not part of the
runtime image. See the [design](../features/schema-driven-api-testing.md) for
the exact three-operation population, independent oracles and non-claims.

## Campaign Entrypoints

The following commands identify the locked campaign entrypoints. Their
portability does not authorize local behavioral execution: use the
[GitHub route below](#run-in-github-actions), following the repository's
[verification placement rule](../architecture/cross-cutting/testing-and-proofkit.md#6-required-gates-and-execution-placement).

```sh
mise run install:backend
mise run test:api
mise run test:api:deep -- --seed 42
```

The default seed is `20260919`. Fast is the ordinary CI profile; deep explores
more inputs without changing the operation set or relaxing any check. The
process deadlines are 180 and 300 seconds, including generation, shrinking and
authentication follow-ups. A timeout or incomplete test process fails.
These commands do not grant an execution-policy exception.

Results are retained in a unique `.api-contract/<profile>-*/` directory:
bounded stdout/stderr, failure diagnostics and `summary.json`. The summary
records the lock digest, tool versions, seed, elapsed time and reaped-child CPU
time. CPU time is a local process measurement, not runner billing or production
capacity. These disposable files are ignored by Git; remove obsolete runs when
their diagnostic value expires.
Exact cases use content-addressed `case-*.json.gz` artifacts, capped at 16 MiB
per decoded case, 32 files and 64 MiB compressed per serial campaign directory.
The 64 KiB text report is separate. A retention limit or storage failure marks
reproduction evidence incomplete without hiding the original test failure.

## Run In GitHub Actions

The normal native pytest population includes the fast cohort without an extra
PR job or post-merge repetition. For a short isolated run, dispatch API Contract
Campaign with a profile and seed. Full Check also calls the same workflow when
`api_contract_deep` is enabled and requires its success. Both publish
`api-contract-exploration`. Normal native failures publish available
`api-contract-<shard>` diagnostics separately from exact shard receipts.
The generator plugin activates only when its test directory is selected;
ordinary unit and mutation invocations avoid loading the heavyweight generator.

```sh
gh workflow run api-contract.yml --ref <branch> -f profile=deep -f seed=42
gh workflow run python-persistence.yml --ref <branch> -f api_contract_deep=true
```

Artifacts are retained for seven days and contain only synthetic test inputs.
The standalone dispatch entry becomes available after its workflow is merged
to the default branch; pre-merge qualification uses the existing Full Check
caller. A standalone green run is never a complete PR or release qualification.
Check the run's tested SHA before using its result for a changed branch. Passing
this cohort does not substitute for the existing coverage, mutation, database,
security or browser gates.

## Investigate A Counterexample

1. Retain the exact source checkout, lock, profile, schema identity, phase and
   synthetic case together. A seed alone does not reproduce changed code.
2. Repeat the same profile and seed on that checkout through the admitted
   GitHub campaign. Hypothesis can shrink
   generated failures; explicit and coverage cases are retained as exact cases,
   not promised to be minimized.
3. Classify the refusal under the operation's actual contract. A schema-valid
   configuration envelope can contain invalid policy bytes and legitimately
   return `422`. Do not globally allow error responses to hide a defect.
4. Reduce a confirmed case to an ordinary owner-specific regression test,
   demonstrate the failure and its positive control, then fix the owner.

## Extend Coverage

Add an operation only with a safe synthetic effect model, a known-valid request
that reaches its intended port, independent invalid/auth cases and a sensitive
response oracle. Review marginal collection and execution cost, including
portable CI's repeated cohort. Durable state-machine and live-provider tests
remain separate proof owners; enabling every library switch is not evidence
that every endpoint is safely or meaningfully covered.
