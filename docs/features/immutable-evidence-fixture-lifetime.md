# Immutable Evidence Fixture Lifetime

Status: bounded test-performance design; native measurements pending

Owner: test fixtures for production relation, cutover and request admission.
The [plan](immutable-evidence-fixture-lifetime-implementation-plan.md) owns
execution and acceptance. This is B3 in the [roadmap](../../ROADMAP.md).

## Decision

Extend the existing module-seed/function-copy pattern from
[coverage cost attribution](coverage-cost-attribution.md) to four measured
test modules. Preserve real fixture construction and every operation under
test. Do not cache production functions or the shared factory.

| Module                                                  | Shared construction                    | Function-local state                                               |
|---------------------------------------------------------|----------------------------------------|--------------------------------------------------------------------|
| `unit/app/test_production_cutover.py`                   | One default `ProductionCutoverFixture` | Copied values, mocks, service, states and call histories           |
| `unit/app/test_production_request_evidence.py`          | One default `ProductionCutoverFixture` | Copied values, stores, sources, successor and foreign-key fixtures |
| `unit/runtime/test_production_evidence_admission.py`    | One default `ProductionCutoverFixture` | Copied values, worker calls, cancellation and monkeypatch          |
| `unit/production_admission/test_production_relation.py` | One `EvidenceFixture`                  | Copied values, subject, signed variants and actual replay          |

## Evidence And Scope

Completed GitHub run `34210133716`, source `5a1413c`, recorded setup totals of
184.01, 73.79, 80.21 and 122.88 seconds respectively, across 25, 10, 11 and 51
cases. Its PostgreSQL job passed 6,749 cases with two skips and 85.16% coverage
in 1,984.70 seconds. These rounded observations identify repeated setup cost;
they do not prove CPU cost or achievable savings. This complete baseline
supersedes partial duration rows from the cancelled run `34204334022`.

The change affects test preparation only. It does not modify DB fixtures,
production admission, signature verification, coverage configuration, test
selection, mutation operators, dependencies or workflow budgets.

## Isolation Argument

For each module, construct baseline `S` once through the unchanged real
factory. For every test `i`, obtain an isolated value graph `C_i`:

```text
value(C_i) = value(S)
mutable_identity(C_i) intersect mutable_identity(C_j) = empty, i != j
mutate(C_i) does not change S or a later C_j
operation_under_test(C_i) executes anew for every test
```

Use standard-library `deepcopy` for ordinary values. The signed fixture also
contains an opaque grant whose registration deliberately rejects copying.
`isolated_copy()` first re-admits the signed bytes through the existing real
verifier, with a fresh fixed clock, then maps the old grant to this fresh grant
in the copy memo. It never copies private authority fields or bypasses their
constructors. Grant and registration identities must differ between cases;
their admitted authority ID, envelope and receipt must match the seed.

The stateless, noncopyable Ed25519 private key is the only object explicitly
mapped to itself. Signing does not mutate it. All other values are copied;
the relation fixture needs no memo exception. This does not cache the factory:
a wrong-key test still constructs a new independent key, and a successor
explicitly requesting the existing key still receives it.

The simpler direct shared-fixture alternative removes copying but introduces
unnecessary aliasing. Deep-copying the crypto key is unsupported. A process-wide
factory cache changes unrelated callers and can invalidate constructor or
monkeypatch witnesses. Module-local seeds plus isolated copies avoid both
problems without a cache framework or new production abstraction.

## Falsifiers And Reconsideration

Native witnesses compare all fixture fields by value, except the opaque grant,
which is checked through fresh identity and its public admitted projections.
They independently corrupt copied staged bytes, policy source bytes and
signed-envelope bytes, then require the baseline and a fresh copy to remain
unchanged. Existing wrong-key, successor-generation, forged-relation,
per-case replay, cancellation and mock-call assertions must still execute.
Static AST comparison preserves all existing test bodies and parameter sets.

Reopen this decision if a fixture gains a mutable crypto wrapper, noncopyable
resource, live time, cleanup requirement, construction-time monkeypatch, or an
observable key-generation count. Do not extend this pattern to another cohort
without its own state and cost analysis. A failed equivalence witness blocks
the optimization, even when elapsed time improves.

Native phase timings, full case/skip inventory and canonical coverage determine
the measured conclusion. One faster hosted runner does not establish general
speedup, CPU saving or production performance. No timing threshold is added to
semantic tests.
