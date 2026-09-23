# Proofkit 0.10 Consumer Admission

Status: accepted dependency decision; provider verification remains required
Date: 2026-09-05
Owner: repository proof-tooling distribution admission

## Decision And Scope

Pin `agentic-proofkit==0.10.1` in the development group. This decision replaces
only the Proofkit version admission in [Python repository tooling](python-proof-tooling.md)
and [Testing and Proofkit](../architecture/cross-cutting/testing-and-proofkit.md).
The latter retains requirement, witness, and provider-evidence authority.
Interpreter, runtime, schema, requirement meaning, workflow, and merge policy
are unchanged. No new CLI command is enabled by this dependency update.

The platform contract remains macOS 13+ and glibc 2.17+ Linux on arm64/x86_64.
Windows and musl remain outside the supported wheel set. The PyPI package
remains a Python carrier of the native Go CLI, not a Python SDK.

## Evidence And Compatibility Argument

Official source endpoints are tags `v0.6.0` at
`1aee607f559a91e8a24a9a54ac6ec4bc179c0fc5` and `v0.10.1` at
`4401e966746b4170d904cfaf23e02dd0514dd536`. All seven complete command records
in `proofkit/cli-contract.v2.json` are equal across those endpoints, and their
native command-owner directories have no source changes:

| Consumer command                | Protected output                              |
|---------------------------------|-----------------------------------------------|
| `repo-profile-admission`        | Exact structural report root                  |
| `requirement-bindings`          | Requirement-to-witness routes                 |
| `requirement-source-admission`  | Requirement source admission                  |
| `requirement-source-transition` | Requirement lifecycle transition              |
| `selective-gate-plan`           | Exact per-path command selection              |
| `text-policy`                   | Text-policy structural report                 |
| `witness-plan`                  | Exact normalized commands and parallel groups |

The shared dispatcher now resolves hierarchical routes. The seven consumers
retain their single-token routes; the registered grammar rejects duplicate or
prefix-ambiguous routes. Process-contract extensions describe additional
commands, not different success or failure semantics for these seven.
Unchanged command owners alone do not prove unchanged dispatcher behavior.

```text
Consumed = the seven commands above
KnownBreaking = {init, agent-route.input.v1, change-workflow-plan}
Consumed intersection KnownBreaking = empty

UpgradeAdmission := ExactPinsAndHashes
  and SameSupportedWheelSet
  and SameConsumedContracts
  and ConsumerStructuralChecksPass

MergeEvidence := UpgradeAdmission
  and ExactHeadRequiredGitHubChecksPass
```

`init` retirement, `agent-route` input identity v2, and the `change plan` rename
do not require migration here. The public CLI contract newly embedded in wheels
allows installed-carrier checks without a source checkout. It still describes
root shapes, not complete nested schemas or native witness truth.

## Alternatives And Consequences

Keeping 0.6.0 avoids change but lacks the installed contract and subsequent
upstream fixes. A bounded upgrade retains our current API while enabling that
contract check. An unpinned or wholesale adoption would enlarge the mutation
and compatibility surface without a consumer requirement; reject it.
The 0.10.1 native-construction fix for repository transactions is retained as
an upstream hardening improvement, not as a claim that this consumer executes
transactional materialization. New adoption and integration commands remain
separate proposals requiring explicit repository authority.

No global absence of upstream regressions, throughput gain, cross-platform
execution, or production readiness follows from these observations.
Reopen admission if any consumed route, input/output, exit behavior, supported
wheel, install side effect, license, security advisory, or package provenance
changes. Revert by an additive exact-pin and lock update if a regression is
confirmed; do not weaken the wrapper or test oracle to accept it.

## Implementation And Verification

1. Update only the exact Proofkit pin and its dependency-admission expectation;
   regenerate `uv.lock` and the hashed development export with uv.
2. Preserve strict output admission, malformed-input and unsupported-flag
   falsifiers. Add an installed-carrier contract check for every used command.
3. Run the existing bounded dependency, requirement, route, text, lint, and type
   witnesses. They do not run behavioral tests or grant provider evidence.
4. Run the existing GitHub Full Check at the exact candidate. Its Python tests
   cover negative CLI inputs, identity transition and both platform families'
   lock projections; unavailable platform execution stays a non-claim.

Writer boundary: package admission owns the pin and wheel set; CLI consumer
tests own route/contract falsifiers; this decision owns only version admission.
Existing requirement `REQ-CI-PROOFKIT-001` routes all three. The cheapest
sufficient whole-chain gate is the existing dependency and selective-plan
pipeline plus its GitHub native tests, not a new workflow or validation engine.

## Feedback Scope

`APF-CI-004` and `APF-CI-008` now distinguish embedded root-contract discovery
from still-open nested-schema and generic-result admission. `APF-CI-007`
describes a consumer-local resolution, not an upstream Python admission API.
The ledger dependency field follows the active pin; retained observation dates
and unchanged records do not imply every historical feedback oracle was rerun.

## Sources

- [PyPI distribution](https://pypi.org/project/agentic-proofkit/0.10.1/)
- [Release 0.10.1](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.10.1)
- [Release 0.7 migration](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.7.0)
- [Release 0.9 migration](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.9.0)
