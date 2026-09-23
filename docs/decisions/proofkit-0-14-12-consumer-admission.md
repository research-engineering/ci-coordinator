# Proofkit 0.14.12 Consumer Admission

Status: dependency decision; native qualification required before merge
Date: 2026-09-13
Owner: repository proof-tooling distribution admission

## Decision

Pin `agentic-proofkit==0.14.12` in the development toolchain. This replaces only
the release selection in [0.14 admission](proofkit-0-14-consumer-admission.md).
The earlier decision and its evidence remain unchanged. Preserve the existing
seven-command boundary, strict output admission and required witness routes.
No product runtime, database, workflow selection or provider authority changes.

Retain all four non-yanked Python-carrier wheels: macOS 13+ and glibc 2.17+
Linux on arm64/x86_64. The wheel still carries the Go CLI; it is not a newly
admitted Python SDK. Registry metadata observed on 2026-09-13 selected this
version and published those artifacts at approximately 16:09 UTC.

## Compatibility Argument

The source comparison binds `v0.14.7` at
`ab1783ed1cccc6ed37fc1c1723d74376d974e8bf` and `v0.14.12` at
`0d3ba3bb157b7b62af98d2034f115e2453bc9eb0`.

Six consumed command records are identical: `repo-profile-admission`,
`requirement-source-admission`, `requirement-source-transition`,
`selective-gate-plan`, `text-policy` and `witness-plan`. Their referenced root
definitions and the common process contract are also identical. The seventh,
`requirement-bindings`, changes only its input root-definition digest: `selection`
remains allowed but is no longer listed as required. Its output is unchanged.
The consumer already supplies `selection` explicitly. Thus, at this declared
root boundary, every previously admitted consumer input remains admitted;
absence of selection is not a newly activated Coordinator behavior.

Shared source changes still require native regression evidence. The update
preserves empty requirement-source identity, strengthens child-process
retirement, centralizes installed route identity equality and checks witness
commands before source discovery. Help improvements concern unconsumed authoring
or receipt workflows and grant no additional authority here. This is evidence
for a bounded upgrade, not binary equivalence, all-platform execution or a
measured performance gain.

## Alternatives And Implementation

Keeping 0.14.7 avoids dependency change but leaves the requested current-release
evaluation and relevant process fixes unapplied. Wholesale adoption of new CLI
surfaces would introduce unrelated contracts. The minimum sufficient change is
an exact pin/lock update with the existing seven-command qualification.

1. Update the development pin, dependency witness and version fixtures. Use the
   repository-pinned uv to regenerate `uv.lock` and its hashed requirements export;
   verify unrelated package records do not change.
2. Match four wheel filenames, hashes, sizes and supported families against the
   exact release metadata. Preserve malformed-input, unsupported-flag,
   scalar-type, output-shape and complete route-set oracles.
3. Update current routing and feedback dependency metadata without rewriting
   historical observations or claiming their open items resolved.
4. Run bounded local dependency/requirements/route/text/lint/type admission.
   Require the existing GitHub consumer tests and exact-head required checks plus
   independent review selected by AGENTS.md before squash merge.

Writer readiness: distribution admission owns package identity/platform facts;
wrappers own invocation/output admission; `REQ-CI-PROOFKIT-001` owns route
coverage. Their protected observations are rejection behavior, exact selected
commands, native-test placement and no new executable authority. A differing
wheel, consumed contract, missing route or changed valid result reopens this
decision. Repair or revert through an additive exact pin/lock change, never a
weakened oracle. New producer materialization commands remain unadmitted.

## Sources

- [Exact PyPI release](https://pypi.org/pypi/agentic-proofkit/0.14.12/json)
- [Complete source comparison](https://github.com/research-engineering/agentic-proofkit/compare/v0.14.7...v0.14.12)
- [Versioned CLI contract](https://github.com/research-engineering/agentic-proofkit/blob/v0.14.12/proofkit/cli-contract.v2.json)
