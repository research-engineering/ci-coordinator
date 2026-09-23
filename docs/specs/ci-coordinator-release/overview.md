# CI Coordinator Release Specification

Status: active requirement package

Owner: `ci-coordinator.release`

## Purpose

This package owns exact-source publication of the CI Coordinator OCI artifact.
It is separate from the runtime, deployment platform, and production-admission
signer because those surfaces establish different facts.

The package also owns the narrow predicate admission profiles used by the
release workflow. `spdx-2.3.schema.json` derives from the
[SPDX schema](https://github.com/spdx/spdx-spec/blob/aadf3b0b8dbbabdb4d880b0fc714255fea436ff7/schemas/spdx-schema.json)
from tag `v2.3`, commit `aadf3b0b8dbbabdb4d880b0fc714255fea436ff7`,
whose upstream SHA-256 is
`239208b7ac287b3cf5d9a9af23f9d69863971102a5e1587a27a398b43490b89b`.
The repository copy changes only two non-normative Unicode description marks
to escaped ASCII quotes and adds the required final newline. Its admitted
SHA-256 is
`23b238cde51ad35021a61eb79639814c91a436b1d62061a1122aba6107b1c927`.
Neither normalization changes a validation keyword or value.
The upstream schema is distributed under the retained
[CC-BY-3.0 license](SPDX-LICENSE.txt); preserve its provenance and attribution
alongside the schema. The project's Apache-2.0 license does not replace these
third-party terms.
The repository-owned schemas add only constraints required by this exact
BuildKit release path; they do not redefine SPDX or SLSA globally.

The current workflow event policy is [CI trigger policy](../../features/ci-trigger-policy.md).

The [minimal runtime and repair admission design](../../features/runtime-base-qualification.md)
defines the current packaging transition and its bounded evidence. Release
schema v2 admits only exact, expiring proved repairs without hiding raw scanner
matches; the separate signer and scheduled scanner never execute the image.

## Authority

```text
requirements.v1.json
  -> proofkit/requirement-bindings.json
  -> repository-native workflow and falsifiers
  -> provider publication receipt
```

The machine-admissible source is `requirements.v1.json`. This overview creates
no additional normative requirement.

## Non-Claims

This package does not claim deployment, rollback, live target-repository
provider behavior, shadow safety, owner approval, CI omission, or production
authority.
