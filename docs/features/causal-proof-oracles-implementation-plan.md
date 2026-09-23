# Causal Proof Oracles Implementation Plan

Status: delivery plan

Date: 2026-09-06

## Scope

Implement the [causal proof refinement](causal-proof-oracles.md), preserving all
runtime behavior and pre-existing design/plan payloads.

## Ordered Work

| Step | Owner/files                                         | Change                                                                                                                             | Acceptance                                                                                                                         |
|------|-----------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| 1    | `test_control_plane_identity_state.py`              | Observe the actual PID/blocker relation before expiry; release after database-observed expiry; include the public clock operation. | Reject pre-fence clock sampling and vacuous late transaction entry; no leaked lock/task.                                           |
| 2    | `test_bootstrap_validator.py`                       | Add canonical wrong-key and changed-signature-byte cases.                                                                          | Actual Node cryptographic verification rejects both; valid signatures remain accepted.                                             |
| 3    | `mutation/pytest_report.py`                         | Admit builtin JUnit output with bounded parsing, phase and identity evidence.                                                      | Missing/malformed/error-phase/duplicate/inconsistent data fail closed; positive and stable-skip reports pass.                      |
| 4    | `mutation_suite_runner.py` and its tests            | Require passing baseline and equal selections; attach pytest evidence to results.                                                  | Native call failure is killed; fixture, collection, skip and identity drift are invalid; process restoration/timing remain intact. |
| 5    | Current indexes and Proofkit routes                 | Bind new owners and witnesses through existing commands.                                                                           | Exact source digests, reachable docs and no dropped predecessor binding.                                                           |
| 6    | Independent review and exact-head GitHub Full Check | Review causal operands and run the native suites.                                                                                  | Required checks pass at the published head; residual non-claims remain explicit.                                                   |

Native report rejection retains a fixed admission diagnostic without XML error
payloads or process output. An existing mutant rejected by the stronger oracle
remains unresolved until its exact failure phase and cleanup are established;
neither exit-code-only fallback nor fixture errors count as kills.

The bootstrap DDL mutants use the explicit `unmigrated_database_url` lifecycle:
the isolated container is a fixture resource, but Alembic and its catalogue
assertions execute in the test call. Ordinary persistence tests retain their
migrated setup and exact runtime ACL admission. Audit mutation witnesses dispose
their engine pools in `finally`, including an early failed assertion, before
fixture teardown checks that the runtime principal has drained.

The same call-owned bootstrap admission covers weakened lower and upper byte
bounds. Direct SQL boundary matrices remain independent witnesses. The full
upgrade/downgrade scenario also owns its initial migration rather than depending
on an already admitted mutated baseline. The database-free publication-order
witness belongs with unit migration-environment tests, outside the autouse
PostgreSQL lifecycle; its mutation route follows that move.

Compatibility interleavings use context-managed UoWs and a task group so a
participant timeout or assertion also releases checked-out transactions and
drains spawned work before engine disposal. Disposing an engine alone does not
close a checked-out UoW. Existing timeout and lock assertions stay unchanged.

## Execution Boundary

Only static local gates are allowed. No local pytest collection, database,
Node/bootstrap execution or process-runner behavior is used as proof. Preserve
unavailable external production and deployment obligations in the roadmap.
