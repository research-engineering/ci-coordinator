# Causal Proof Oracles

Status: implementation refinement

Date: 2026-09-06

## Decision

Strengthen three existing witnesses whose earlier outcomes did not uniquely
identify the claimed guard. Runtime session, signature, planning and provider
policy remain unchanged. The [testing specification](../architecture/cross-cutting/testing-and-proofkit.md)
owns witness meaning; this refinement owns the concrete distinctions below.
The [implementation plan](causal-proof-oracles-implementation-plan.md) owns delivery.

| Boundary                                   | Insufficient observation        | Required distinction                                                                                            |
|--------------------------------------------|---------------------------------|-----------------------------------------------------------------------------------------------------------------|
| Session time after the compatibility fence | Sleep, then an expired result   | The actual transaction waited before expiry and resumed after expiry.                                           |
| Bootstrap signature                        | Noncanonical signature rejected | Canonically encoded, correctly sized bytes fail under the wrong key or a changed signature bit.                 |
| Pytest mutation                            | Process exit `1`                | A call-phase failure in the same test identity set as a passing baseline, without fixture or collection errors. |

These are bounded counterexample closures, not proof of every test or mutant.

## Session Fence

Let `T` be the actual participant transaction, `B` its blocker, `E` the session
expiry, and `K` the database statement clock. The witness must establish:

```text
transactionStarted(T)
  -> observed(B in pg_blocking_pids(pid(T))) and K < E
  -> observed(K >= E)
  -> release(B)
  -> public session operation completes
```

A test-only observer captures the actual connection PID immediately before
delegating to the unchanged compatibility fence. It does not replace the lock,
database clock or admission predicate. Polling is bounded; sleeps only pace
queries. A slow fixture that first enters after expiry fails its precondition
instead of passing vacuously. Pending tasks are cancelled/drained and the blocker
is released on exceptional paths.

`load` must return no expired session; `replace` must reject insertion. A
separate `current_time` case must return a value at or after `E`: this isolates
the clock sample from the independent SQL guard on insertion. No claim that
one public outcome proves every internal time check is made.

## Cryptographic Rejection

Keep the payload, declared key ID, algorithm, canonical encoding and 64-byte
signature length valid. Separately change the trusted public-key material and
one decoded signature bit. Invoke the existing Node bootstrap verifier, not a
Python substitute, and require its invalid-signature fallback outcome. Retain
valid-key positive witnesses so unconditional rejection cannot pass.

## Pytest Evidence

Use pytest's builtin [JUnit XML report](https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files),
with `junit_family=xunit1` to retain file identity. Its
[reporter](https://docs.pytest.org/en/stable/_modules/_pytest/junitxml.html)
distinguishes call failures from setup, teardown and collection errors.
No custom plugin, log parser or new dependency is needed.

The runner owns one freshly removed report path per invocation. The adapter
admits at most 8 MiB, 100,000 test cases, depth 8 and 400,010 XML elements.
The standard-library `XMLParser` uses a
[custom TreeBuilder](https://docs.python.org/3.13/library/xml.etree.elementtree.html#xml.etree.ElementTree.TreeBuilder)
that rejects every document type before entity declarations and enforces the
structural bounds. No alternate parser entrypoint or external resolver is used.
The narrow parser-warning suppression documents this guard, not blanket XML
safety. Native negative cases cover DTDs, UTF-16, depth, size and malformed data.

An identity is the full tuple `(file, classname, name)`. Identities must be
nonempty and unique. Let `P`, `F`, `S`, `X` be passed, call-failed, skipped and
error cases. Suite counters must equal the observed cases. Define:

```text
BaselineValid := exit = 0 and |P| > 0 and F = X = empty
SameSelection := executedIds_mutant = executedIds_baseline
                 and skippedIds_mutant = skippedIds_baseline
Killed := BaselineValid and SameSelection
          and exit_mutant = 1 and |F_mutant| > 0 and X_mutant = empty
Survived := BaselineValid and SameSelection
            and exit_mutant = 0 and F_mutant = X_mutant = empty
```

Store canonical identity-set hashes rather than repeating all identities in
every mutation report. This relies on the existing SHA-256 collision-resistance
assumption. Missing, stale, nonregular, malformed, contradictory or partial
reports, duplicate identities, changed selection and unsupported pytest exits
are `invalid`, never killed. A baseline containing only skips is invalid;
stable skips alongside real passing tests are allowed.

Report-option overrides and early-stop flags are rejected because they can
replace the report authority or truncate the compared identity set. Existing
finite manifests do not use those flags. Non-pytest and Vitest classification,
process deadlines, restoration and signal cleanup retain their prior meaning.

## Cost And Revision Conditions

Exit codes alone are smaller but cannot distinguish fixture failure. A new
pytest hook adds plugin-loading authority; a general reporting framework adds
unneeded ownership. The selected adapter uses builtin reporting and a bounded
standard parser, at the cost of one small report write/read per invocation.

Revisit if parallel pytest workers, retries, early-stop semantics, custom test
identity attributes or external reports become required. Do not silently relax
identity equality to admit them. This is trusted finite mutation evidence, not
an attestation against a hostile test process forging its own report.

## Verification Boundary

Ruff, mypy, source boundaries, docs/JSON and Proofkit admission run locally.
Native PostgreSQL, Node, pytest and mutation execution run only in GitHub.
Independent review follows the current root `AGENTS.md` policy. Green routes do
not prove behavior; native success does not prove production readiness.
