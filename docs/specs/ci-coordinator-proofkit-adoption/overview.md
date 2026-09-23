# CI Coordinator Proofkit Adoption Specification

Status: active requirement package

Owner: `ci-coordinator.proofkit-adoption`

## Purpose

This package owns how CI Coordinator consumes
`agentic-proofkit`. Proofkit is a reusable proof
infrastructure dependency. CI Coordinator remains the authority for product
requirements, native witness execution, proof freshness, merge policy, rollout,
and production readiness.

## Boundary

Proofkit may validate caller-owned requirement sources, proof bindings, witness
plans, selective-gate inputs, native regression-vector routing, and bounded
agent guidance. It must not become runtime omission authority.

## Non-Claims

This package does not claim Proofkit can generate product truth, execute native
CI, approve merge, or prove deploy readiness by itself.

## CI Matrix And Self-Consumer

`REQ-CI-PROOFKIT-012` binds the [CI matrix decision](../../decisions/comprehensive-ci-matrix.md)
to its exact command catalog, bounded file assignment, utility result admission,
native gates and generated self-consumer parity. The [operation guide](../../how-to/coordinate-this-repository.md)
owns regeneration and deployment qualification steps. The [utility catalog](../../../tooling/quality/README.md)
owns each utility predicate and its exact exceptions. Static assignment does not
prove test adequacy or authorize omission. The native required gate remains
independent until the existing production-admission contract is satisfied.

## Proofkit 0.14.18 Revalidation

The development dependency is `agentic-proofkit==0.14.18`. This version selection
supersedes the pin in the [0.14.12 decision](../../decisions/proofkit-0-14-12-consumer-admission.md),
whose historical bytes and qualification boundary remain unchanged. The bounded
upgrade preserves the existing seven-command consumer integration; it does not
adopt new materialization, browser, receipt or provider-enforcement commands.

Official release observations on 2026-09-20 cover the complete interval:

| Release                                                                                   | Relevant change                                                      |
|-------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| [0.14.13](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.14.13) | Source-view search and layout corrections.                           |
| [0.14.14](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.14.14) | Lazy native-evidence traceability guidance.                          |
| [0.14.15](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.14.15) | Private source-codec coordinate regression controls.                 |
| [0.14.16](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.14.16) | README workflow clarification.                                       |
| [0.14.17](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.14.17) | Lazy declaration-coverage and receipt-currentness recipes.           |
| [0.14.18](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.14.18) | Source-compatibility regression boundaries; no public codec cutover. |

The installed Python carrier's `proofkit/cli-contract.v2.json` has SHA-256
`40e21bb074eea37dba970d08cf06e1c5ee0ec1c1d7404fad1e7e28490a3924c4`
and equals the [0.14.18 contract](https://github.com/research-engineering/agentic-proofkit/blob/v0.14.18/proofkit/cli-contract.v2.json).
All seven consumed command records, their fourteen referenced input/output root
definitions and the common process contract equal the corresponding 0.14.12
records: `repo-profile-admission`, `requirement-source-admission`,
`requirement-source-transition`, `requirement-bindings`, `selective-gate-plan`,
`text-policy` and `witness-plan`. This is a contract comparison, not whole-binary
or runtime equivalence. Complete nested schemas remain explicitly outside those
root definitions.

Every APF-CI-001 through APF-CI-017 record in the [feedback ledger](../../../proofkit/adoption-feedback.v1.json)
has a current disposition. The original observation date remains historical.
Consumer-owned resolutions APF-CI-002, 006 and 007 remain scoped to their
wrappers and validators; native regression execution remains required. APF-CI-004
and 008 retain partial root-contract discovery. APF-CI-005 retains its historical
capability-ladder request without asserting a current producer defect. Other
open records describe unclosed consumer oracles, not a claim of universal
producer incapability.

APF-CI-009 was specifically rechecked with the installed CLI and a closed input
set: invariants containing `The bearer must remain external.`,
`The risk-owned boundary remains explicit.` and
`The authorization token must be validated.` were admitted. A separate synthetic
credential-shaped literal was rejected; the field-level diagnostic omitted its
value. The finite recorded oracle is resolved. No claim is made about the first
fixing release or exhaustive secret recognition. `witness-plan` help still exposes
only root-shape coverage, so APF-CI-010 remains open.

Release descriptions, source contracts and these bounded structural checks do
not replace the exact candidate's GitHub native consumer tests, dependency
admission, required checks and independent review. A changed consumed contract,
wheel identity, result algebra or native regression reopens admission.
