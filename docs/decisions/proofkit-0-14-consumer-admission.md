# Proofkit 0.14 Consumer Admission

Status: dependency decision; exact-head native verification remains required
Date: 2026-09-10
Owner: repository proof-tooling distribution admission

## Decision

Pin development dependency `agentic-proofkit==0.14.7`. This supersedes only the
release selection in [0.10 admission](proofkit-0-10-consumer-admission.md), whose
historical evidence stays unchanged. Retain the existing seven-command consumer
boundary, strict output admission and native witnesses. Do not activate project
view, agent integrations, transactional materialization or a requirement codec
merely because the package now includes them.

The supported wheel set stays macOS 13+ and glibc 2.17+ Linux, each on arm64 and
x86_64. Python remains a carrier of the Go CLI, not a newly admitted Python SDK.
Runtime dependencies, CI selection and product behavior are unchanged.

Consumer repair: compare normalized witness-plan values with deterministic
standard-library JSON encoding, with sorted object keys and finite numbers.
Python container equality collapses `true` with `1` and `false` with `0`;
those substitutions must not satisfy the existing exact-projection contract.
Preserve JSON scalar types and array order while ignoring object-key order.
This tightens rejection of malformed output, not the seven-command authority
or valid repository inputs. It does not imply a complete nested-schema SDK.

## Evidence And Argument

The official PyPI response observed on 2026-09-10 selects 0.14.7 and lists four
non-yanked wheels, published on 2026-09-09. Their names, sizes and SHA-256 values
must equal the regenerated lock. The registry response contains no listed
vulnerability; that is not proof of absence of vulnerabilities.

Compare the complete consumed command records and process contract from
`proofkit/cli-contract.v2.json` at source tags:

- `v0.10.1`: `4401e966746b4170d904cfaf23e02dd0514dd536`;
- `v0.14.7`: `ab1783ed1cccc6ed37fc1c1723d74376d974e8bf`.

All seven command records and the process contract are equal, including the
declared input/output root digests and command-owner source digests:
`repo-profile-admission`, `requirement-bindings`,
`requirement-source-admission`, `requirement-source-transition`,
`selective-gate-plan`, `text-policy` and `witness-plan`.
Shared dispatcher and other kernel changes still require consumer validation;
record equality is not binary equivalence or native regression proof.

```text
CandidateAdmission = ExactPinAndRegistryHashes
  and SameSupportedWheelSet
  and SameConsumedContracts
  and ExistingConsumerStructuralChecksPass

MergeAdmission = CandidateAdmission and IndependentReview
  and ExactHeadRequiredGitHubChecksPass
```

The release improves authoring reference guidance and includes newer project
navigation and integration work. Those unconsumed surfaces do not change our
authority model. Desired-absence transaction journals and receipts have their
own downgrade restrictions; this repository does not invoke that producer.
The selected source-v2 codec and complete nested CLI schemas remain unadmitted.

## Alternatives And Plan

Keeping 0.10.1 avoids package change but does not satisfy the requested current
release. An exact bounded upgrade uses the existing validation boundary;
wholesale adoption would add unrelated behavior and proof obligations. Keep this
independently reversible dependency change separate from observation storage/UI.

1. Change the exact development pin and dependency expectation; regenerate
   `uv.lock` and the hashed requirements export with the admitted uv toolchain.
2. Preserve installed-carrier, malformed-input, unsupported-flag, output-shape
   and platform-family witnesses. Update exact-version fixtures and add isolated
   boolean/integer and integer/float projection falsifiers, retaining the valid
   reordered-key control. Use the existing JSON library, not a new validator.
3. Run existing bounded dependency, requirement, route, text, lint/type and
   documentation checks. These do not execute native test suites.
4. Require the existing GitHub consumer/native checks and independent reviewer
   selected by `AGENTS.md` before squash merge. No new workflow is needed.

Writer ownership: dependency admission owns pins/wheels; wrappers own strict
command results; `REQ-CI-PROOFKIT-001` owns the proof routes. The feedback ledger
dependency field follows the candidate, but retained observations keep their
original dates and do not imply a new audit of every feedback item.

Reopen admission on changed consumed semantics, platform support, hashes,
installation effects or a concrete regression. Roll back by an additive exact
pin/lock change, never by weakening an oracle. This does not roll back state
created by separately adopted upstream commands.

## Non-Claims

No global upstream regression absence, all-platform execution, new capability
adoption, performance improvement, production readiness or observation rollout
is established by this dependency decision.

## Sources

- [PyPI 0.14.7 metadata](https://pypi.org/pypi/agentic-proofkit/0.14.7/json)
- [Release 0.14.7](https://github.com/research-engineering/agentic-proofkit/releases/tag/v0.14.7)
- [Source changes](https://github.com/research-engineering/agentic-proofkit/compare/v0.10.1...v0.14.7)
