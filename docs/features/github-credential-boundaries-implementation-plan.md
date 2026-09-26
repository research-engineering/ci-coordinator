# GitHub Credential Boundaries Implementation Plan

Status: implementation and validation plan
Date: 2026-09-26

Design: [selected credential boundaries](github-credential-boundaries.md).
Contract: [GitHub integration module](../architecture/modules/github-integration.md).
The [roadmap](../../ROADMAP.md) remains the sole execution register under CI-054.

## Ordered Integration

1. Bind the candidate to the current App profile, all 30 closed operations and
   16 production factory-call relations. Freeze existing shape, identity,
   pagination, outcome and resource contracts before changing credential keys.
2. In `integrations/github`, require the repository keyword in `transport.py`
   and `app_transport.py`; derive the finite read purpose only after existing
   shape admission in `installation_request_admission.py`. In
   `app_credentials.py`, use one private immutable full-grant key for every
   lifecycle map and the exact mint body. Retain all global limits.
3. Bind the 16 calls in the existing capacity, economics, history, measurement,
   observation, governance, production-authority, inventory, reconciliation,
   repository-context, reviewer-permission and workflow adapters. Inventory
   alone supplies `None`; organization callers still supply their independent R.
   Update actual fake signatures and recorded operands, not just type aliases.
4. Qualify independent literal oracles below through the actual factory and
   consumer boundaries. Do not import the production permission map to compute
   expected grants or replace organization population with a narrowed fixture.
5. Keep public trust/provenance instructions in the existing
   [measurement guide](../how-to/measure-and-compare-ci.md) and environment
   example. Pin producer/receiver v1 audience bytes independently; do not change
   workload trust, recipient authority or producer identity to simplify upload.
6. Link the new design/plan through the module. Integrate current requirement
   wording and exact witness routes under existing runtime owners; preserve all
   pre-existing design/plan payloads. Root owns derived metadata and publication.
7. Freeze the complete candidate for independent review under
   [AGENTS.md](../../AGENTS.md). Use the repository
   [verification placement rule](../architecture/cross-cutting/testing-and-proofkit.md#6-required-gates-and-execution-placement):
   source/static review is not native qualification. Behavioral regression and
   final required gates must run in GitHub against the integrated candidate.

## Causal Acceptance Matrix

Test paths below are under `backend/tests/unit` unless prefixed `scripts/`.
All rows require positive controls and counterexamples; authoring is not passing.

| Claim | Existing witness owner and discriminating observation |
| --- | --- |
| Exact operation grants | `integrations/test_github_app_transport_protocol.py`: 30 independently literal requests yield exact permission/repository bodies; unknown shape and scope mismatch yield zero mint and zero API exchange |
| Caller context is real | Existing adapter tests plus `app/test_candidate_planning.py`: all 16 calls expose exact `(I,R)` or inventory `(I,None)` to required-keyword fakes |
| Sharing is neither too broad nor fragmented | `integrations/test_github_app_transport_auth.py`: distinct I/R/P remain separate; equal grants reuse; equal O grants across R share one refresh without sharing request results |
| Denial cannot broaden authority | Same auth owner: preloaded other grants and denied scoped mint yield failure without token substitution or a broader mint |
| Lifecycle and bounds survive rekeying | Auth and `integrations/test_github_app_transport_lifecycle.py`: mixed-key global refresh/cache limits, deadline while waiting, clock resampling, waiter cancellation, callback cleanup and close/drain |
| Organization population survives transport | `integrations/test_github_runner_capacity.py`: real factory mint and full controlled organization membership retain the existing visibility, population and eligibility result |
| Historical audience bytes remain exact | `target_artifacts/test_measurement_report_upload.py`: producer and receiver agree with an independent literal v1 vector |
| Recipe recipients are explicit | `scripts/tests/test_operational_documentation.py`: parse the actual example and environment source; assert the approved audience/endpoint relationship and existing permission scope |

## Qualification Boundary

Native controlled-wire tests can prove local selection and lifecycle behavior,
not a live GitHub grant or organization population. Effective O-only implicit
metadata breadth and numeric repository metadata route compatibility require
separately authorized provider evidence. Until then they remain explicit gaps;
no CI fixture or static count closes them and no failed qualification permits
broader authority. No local behavioral execution, credential request, provider
grant change, deployment or measured cold-path benefit is authorized by this plan.
Changes to public schemas, persistent state, report identity, grants or ownership
are outside this bounded integration and require fresh admission.
