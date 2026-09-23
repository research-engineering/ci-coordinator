# Inspect Retained History Details

The repository History view separates permanent attempt/job statistics from
optional step detail. Reading either requires current repository audit access;
changing collection or retention requires configure access. This guide describes
the candidate capability, not evidence that a deployed image already provides it.

## Import And Inspect

1. Open the repository's CI economics History view and inspect its saved policy.
2. Enable an admitted finite or forever detail policy when additional drill-down
   is wanted. Disabled policy does not remove already retained detail.
3. Let configured collection visit the attempt. For existing summary-only
   records, explicitly request the existing non-destructive rescan; changing the
   policy does not automatically revisit them.
4. Select the exact archived attempt and request its step details. The view
   shows numbered step outcomes and timestamps for each retained provider job.

Step names, logs, artifacts, arbitrary provider JSON and actor profiles are not
part of this payload. Missing or malformed optional step data does not discard
independently valid permanent statistics. Partial job populations and oversized
detail remain unavailable rather than being presented as complete detail.
The finite payload limit is 262,144 canonical bytes, with at most 256 steps per
job and the existing 2,000-job attempt bound.

The first successful durable detail import starts its retention clock. A
summary-only import, failed transaction or repeated import does not reset it.
An enabled policy can admit a first import for a never-imported record, but
ordinary rescan cannot restore expired or explicitly erased detail. Extending
policy does not resurrect deleted content.

## API

Use the existing authenticated control-plane bearer or browser session boundary:

```text
GET /api/v2/economics/repositories/{installation_id}/{repository_id}/history/attempts/{workflow_run_id}/{run_attempt}/detail?generation={generation}
```

Read the current dataset generation first. The new response is scoped to one
attempt and a versioned detail representation. The existing archive-read API
remains unchanged. Do not reuse an old generation after a dataset transition,
or treat unavailable, not-imported and expired states as an empty successful
step population. Responses use no-store and remain subject to authorization,
bounded-response and current-dataset admission.

## Expiry And Troubleshooting

Pausing collection does not pause expiry. Runtime maintenance removes at most
one bounded batch per invocation while preserving attempt/job statistics and
the first-import anchor. Logical expiry hides expired content even if physical
cleanup is delayed; the latter can still retain charged storage bytes.

Repeated cleanup failures or timeouts produce the
`CIHistoryDetailCleanupFailuresObserved` warning. Use the
[observability runbook](operate-production-observability.md) for its exact
observation/pending/hold semantics. Neither an empty batch nor a service-level
warning proves provider completeness, a failed CI run or loss of permanent
statistics. Full-dataset erasure and backup restore are separate operations and
are not implemented by detail expiry.
