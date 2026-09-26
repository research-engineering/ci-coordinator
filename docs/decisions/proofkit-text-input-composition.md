# Bounded Proofkit Text Input Composition

Status: current consumer decision; native qualification required before merge
Date: 2026-09-26
Owner: ci-coordinator.proofkit-adoption

## Scope And Decision

Keep `agentic-proofkit==0.14.18` and its seven consumed commands. Compose only
the existing `text-policy` command over deterministic bounded inputs. The
[adoption specification](../architecture/cross-cutting/proofkit-adoption.md)
owns the reusable-mechanics boundary; `REQ-CI-PROOFKIT-006` and
`REQ-CI-PROOFKIT-008` own admission and finite-command obligations in the
[requirement source](../specs/ci-coordinator-proofkit-adoption/requirements.v1.json).
This decision owns composition rationale and proof boundaries, not a new policy.
It supersedes no earlier design or implementation plan and has no companions.

The pinned native JSON reader limits one input to 33,554,432 bytes, including
when using a file or selecting a JSON pointer. Base64 inventory content can
exceed this limit even with compact JSON. The command offers neither text-content
nor path-reference input to remove that expansion. Its admitted text rules are
per-file; the consumer must preserve global identity and count closure.

## Composition Contract

1. Capture the sorted unique Git inventory G of tracked and nonignored untracked
   paths. Partition G exactly into included I and explicit prior exclusions X.
   Preserve missing paths, dist/, directories, non-regular entries, files above
   1,000,000 bytes and NUL-containing content as exclusions, not checked text.
   Freeze included bytes, path, mode, classification and digest. Do not silently
   deduplicate, relabel or drop paths to make the input fit.
2. Partition I into contiguous maximal prefixes. Every batch is exactly one
   compact UTF-8 JSON document of at most 33,554,432 bytes, including its complete
   envelope and deterministic report ID. Verify rendered byte size before launch.
   Never split a file; an oversized singleton fails. Empty I still invokes the
   native command once. A single batch keeps the original report ID.
3. Resolve the same executable once and invoke sequentially without retry using
   the existing finite-command owner and 8 MiB output cap. One monotonic 120-second
   deadline covers resolution, capture, framing, calls and final checks. Each
   call receives only positive remaining time, not a fresh budget.
4. Admit each complete native report before projection: exact schema, kind,
   report ID, passed state, policy, non-claims and single expected passed rule.
   Require the failures diagnostic containing an empty list, both failure counts
   zero and exact integer summary counts; booleans and floats are not integers.
   Expected input=N, binary-skipped=B, missing-skipped=0 and checked-text=N-B.
   B follows the pinned Go extension/case semantics, including dotfiles; it is
   accounting only. Every included row still reaches the native policy owner.
5. Require exact ordered membership conservation, report-count closure and final
   source recapture equality before aggregate PASS. The aggregate is a consumer
   report, retaining its identity and existing fields, with truthful native call
   count and inventory/batch receipts. No partial aggregate can pass.

Invocation timeout/output failures and post-call deadline failure retain the
exact batch ID and original cause. Preparation deadline failure remains global.
Cancellation is not broadly caught; no retry or alternate policy is introduced.
Unsupported arguments still fail before output. The source-input CLI still emits
one document, not a stream. The other six commands and their admission remain
unchanged. All lexical, path and content decisions remain in the pinned CLI;
the consumer introduces no duplicate lexical checker.

## Alternatives And Cost

| Alternative | Disposition |
|---|---|
| File input, pointer or smaller existing representation | Same pinned limit; no supported text/path-reference primitive. |
| Omit rows, raise the cap or change policy | Rejected: changes coverage or producer resource admission. |
| Upgrade solely to avoid composition | Not required by this decision; upgrades need separate seven-command qualification. |
| Copy lexical rules or add a general batching framework | Rejected: duplicates authority or adds unnecessary mechanism. |
| Bounded sequential native composition | Selected: preserves per-file decisions with explicit global closure. |

[Inventory/framing](../../scripts/proofkit_inputs.py) stays with its existing
owner. [Text composition](../../scripts/proofkit_text_policy.py) owns strict
child-report admission, deadline and aggregate closure, keeping these out of
the generic [wrapper](../../scripts/proofkit_admission.py). Each row size is
computed once; only the current batch is serialized. Frozen content and final
recapture still cost memory; this is not streaming or an RSS bound.

## Qualification And Reversal

The [focused native witnesses](../../scripts/tests/test_text_policy_batches.py)
must establish literal positive counts and whole-input versus forced-partition
parity on the same pinned CLI. A real encoded input above 32 MiB must fail as
one document and pass only as a complete partition. A bad last batch must block
PASS for UTF-8, ASCII, newline and whitespace violations.

Require cap-1/cap/cap+1 and escaped-envelope cases, oversized singleton and empty
input, duplicate/order/overlap/omitted-tail guards, and a valid complete-partition
positive. The omitted-tail oracle must reach only that guard: correct report ID
and independently authored full envelope size are prerequisites, not SUT outputs.
Require forged identity/policy/non-claims/rule/diagnostic/count/type rejection,
second-batch timeout/output failure with cause, cancellation identity, controlled
shared-deadline exhaustion and add/delete/modify/reclassification conservation.
The [existing CLI witnesses](../../scripts/tests/test_proofkit_cli.py) retain
seven-command compatibility. Static routes do not execute these witnesses.

`python.test` owns native execution; `text.policy`, `requirements.admission`,
`selective.plan`, `documentation.graph`, lint/type and generated-contract checks
retain their existing roles and execution-placement rules. Exact candidate
native qualification and independent review are required before merge.

Reopen this decision if a producer primitive can preserve complete inventory,
policy, counts, error identity and bounds with less consumer machinery, or if
the pinned report/extension contract changes. Reversion restores the monolithic
capacity failure; it cannot justify omitted files or relaxed admission.
[APF-CI-018](../../proofkit/adoption-feedback.v1.json) remains open: consumer
composition is mitigation, not a producer fix or an upstream publication.

## Non-Claims

32 MiB bounds each invocation, not total run bytes. The shared deadline is
cooperative and inherits process cleanup semantics; it is not an absolute OS
drain bound. Pre/post conservation is not ABA-proof or a filesystem sandbox.
Excluded content is not text-checked or completely fingerprinted; native binary
suffix skips are distinct from prior exclusions. This does not repair historical
coverage omissions, prove secret scanning, performance, RSS, provider execution,
published-wheel equivalence or production readiness. No native pass follows from
source review, metadata admission or generation alone.
