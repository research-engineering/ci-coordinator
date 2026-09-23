# Observe Repository Actions Runs

Use continuous observation to register eligible completed GitHub runs and
collect their available timing evidence without changing repository workflows.
Observation does not enable selective CI or measure process CPU automatically.

## Prerequisites

- Deploy the observation-capable image after the fenced database migration and
  runtime-role grant transition. Follow the
  [rollout contract](../features/repository-observation.md).
- Install the shared GitHub App for the repository and grant Actions read
  access. The repository must be admitted to the coordinator control plane.
- Sign in with a current administrator who has repository `configure` access.
  Read-only inspection requires `audit` access. App visibility alone does not
  grant either permission.

## Enable Observation

1. Open the repository, then **CI economics > Observation**.
2. Select **Observation enabled** and either **All workflows** or **Selected
   workflows**. Selected mode requires between 1 and 32 provider workflow IDs;
   use the bounded catalogue pages to choose them.
3. Choose **Initial history**: recent runs only or 1 to 6 days before activation.
4. Select **Save observation**. Treat only a confirmed saved revision as success.
5. Inspect scan progress and **Registered runs** separately. Registration is not
   proof that a terminal measurement snapshot has already been captured.

The saved configuration survives closing the browser and service restart.
The visible enabled view refreshes every 15 seconds after successful reads.
An unavailable read retains explicitly stale evidence and stops automatic retry;
use refresh after resolving access or provider availability.

## Change Or Pause

Change the workflow selection or clear **Observation enabled**, then save.
Pausing prevents later discovery commits; already registered runs may continue
collecting, and retained evidence is not deleted. Resuming or changing the
configuration starts a new revision with its selected initial history.

If another administrator changed the revision, explicitly reload the saved
configuration before editing. A failed or timed-out save can have an unknown
outcome: use **Retry same operation**, which resends its exact identity and
payload, rather than inventing a second command. A confirmed conflict is not an
unknown write and requires a fresh read. Do not interpret a browser reload as
confirmation of an earlier command.

Configured IDs missing from the current catalogue page remain configured.
A missing workflow or failed provider request is not evidence of deletion.

## API Equivalent

Use the existing control-plane bearer flow described in
[measurement API prerequisites](measure-and-compare-ci.md). Read:

```text
GET /api/v2/economics/repositories/{installationId}/{repositoryId}/observation
GET /api/v2/economics/repositories/{installationId}/{repositoryId}/observation/workflows?pageNumber=1
GET /api/v2/economics/repositories/{installationId}/{repositoryId}/observation/gaps?limit=20
```

Send this shape to `POST /api/v2/economics/observation` as exact
`Content-Type: application/json`, replacing IDs and the unique operation ID.
Use revision `0` only when there is no saved configuration; otherwise use the
revision from the current read. Browser-session mutations additionally require
the existing same-origin CSRF proof.

```json
{
  "installationId": 101,
  "repositoryId": 202,
  "operationId": "observe-20260910-001",
  "expectedRevision": 0,
  "configuration": {
    "enabled": true,
    "selector": {"kind": "all", "workflowIds": null},
    "backfillDays": 1
  }
}
```

Selected mode uses `{"kind":"selected","workflowIds":[123,456]}`. To pause,
submit the current configuration with `enabled: false`, its current revision
and a new operation ID. Reuse an operation ID only for the identical original
command, including its revision. HTTP `200` distinguishes committed from
replayed; `409` reports an explicit conflict or capacity refusal. Neither an
authorization error nor service unavailability is successful configuration.

Gap pages allow limits 1 to 50 and return a continuation cursor. Workflow
catalogue pages are 1 to 20, with at most 100 entries each and explicit
exhausted, next-page or truncated status. These are independently observed
pages, not one immutable provider inventory.

## Interpret Coverage And Cost

- The source-created eligibility window is seven days. The initial-history
  control does not promise unlimited archival import or every historical rerun.
- Provider scans use bounded subwindows and pages. Saturation, lost outage
  windows and source conflicts are recorded as gaps, not hidden completeness.
- Each repository has at most 10,000 retained source slots and 256 retained gap
  details. The service admits at most 256 observation configurations, including
  paused ones. At the gap-detail cap, the loss watermark remains visible;
  absence of detailed gaps alone does not prove complete history.
- Source evidence keeps its existing source-time retention. Gap expiry is tied
  to the original scan cycle; replay cannot extend it.
- Provider wall time, queue time and summed job duration are not process CPU or
  causal savings. Add the optional
  [measurement producer](measure-and-compare-ci.md) for explicitly scoped CPU
  evidence. Observation alone never changes a workflow or authorizes omission.

See the [design and failure model](../features/repository-observation.md) for
exact limits, persistence rules and qualification boundaries.
