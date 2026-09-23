# Measure And Compare A CI Command

Status: measurement procedure

See the [roadmap](../../ROADMAP.md) for capability-specific native, deployment
and qualification evidence. An observed DEV delivery does not establish
production capacity or measured consumer savings.

## Outcome And Prerequisites

Retain measurements from two explicit run attempts, inspect their provenance
and compare compatible reports. This does not enable selective CI or change
the final gate. Start with the [measurement contract](../features/ci-economics-measured-comparisons.md)
for scope and limitations.

You need a connected instance with the current economics storage capability,
the repository in
its deployment scope allowlist, and GitHub App read access to repository and
Actions metadata. An operator needs a short-lived Keycloak workload token with
`audit` for reads and `configure` for registration. Browser callers instead use
their current session, exact public origin and CSRF token. Never give the
operator token to an untrusted CI job.

The producing job needs `id-token: write`, `actions: read`, and an allowed
Actions workflow identity under the service's existing OIDC configuration.
The report audience is derived from the configured plan audience; it is not
interchangeable with a plan token. Initial support is GitHub.com, not GHES.

Examples use `COORDINATOR_URL`, `INSTALLATION_ID`, `REPOSITORY_ID`, `RUN_ID`,
`RUN_ATTEMPT` and `CONTROL_PLANE_ACCESS_TOKEN` from the operator environment.
Use actual positive IDs, not example repository names, to identify the scope.

## Browser Journey

Open a repository, then **CI economics**. **Registered runs** lists retained
independent sources. Open one to see provider durations and the report list;
open a report to inspect its command counters. Choose **Use as baseline**,
return to the run list, select another report and choose **Use as treatment**.
**Compare 2/2** shows both identities, incompatibilities and server-calculated
descriptive differences. Changing repository or session clears this selection.

For a run not yet registered, use **Discover runs**, enter a UTC creation
window of at most seven days and press **Search**. Discovery needs `audit`;
**Register** additionally needs `configure`. Registration starts collection,
not a GitHub workflow, and does not imply measurements already exist.
**Reconciled attempts** retains the earlier reconciliation-derived view.

The report's **One-report budget** accepts a counter and nonnegative integer
microsecond limit. **Evaluate** is read-only and does not save an alert.
Unavailable evidence, source conflict, missing/expired reports, access denial
and provider failure are not successful measurements. Refresh is explicit.

The same catalog is available without UI:

```text
GET /api/v2/economics/repositories/{installationId}/{repositoryId}/sources
GET /api/v2/economics/repositories/{installationId}/{repositoryId}/sources/{sourceId}/reports
```

Both accept `limit` (1-100) and optional `afterCursor`. Pass the returned
`nextCursor` unchanged. Pages are independent current reads, not a population
snapshot; refreshing is necessary to discover newer sources. The report list
contains metadata only. Full evidence may expire between list and detail reads.
See the [console contract](../features/economics-operator-console.md) for
identity, resource and uncertainty boundaries.

## 1. Discover And Register An Attempt

`POST /api/v2/economics/source-discovery` accepts `installationId`,
`repositoryId`, `createdFrom`, `createdThrough` and `pageNumber`. Choose a
bounded UTC interval within the last seven days. Each response describes one
page, its provider total and termination, not all historical attempts.

Register each exact attempt before its reporting command finishes:

```sh
jq -n \
  --argjson installationId "$INSTALLATION_ID" \
  --argjson repositoryId "$REPOSITORY_ID" \
  --argjson workflowRunId "$RUN_ID" \
  --argjson runAttempt "$RUN_ATTEMPT" \
  '{installationId:$installationId, repositoryId:$repositoryId,
    workflowRunId:$workflowRunId, runAttempt:$runAttempt}' |
curl --fail-with-body --silent --show-error \
  --request POST --header "Authorization: Bearer $CONTROL_PLANE_ACCESS_TOKEN" \
  --header 'Content-Type: application/json' --data-binary @- \
  "$COORDINATOR_URL/api/v2/economics/sources"
```

Registration resolves the current attempt again; discovery does not grant
write authority. The returned `registered` or `replayed` source records its
exact REST head, creation time and evidence digest. No reconciliation or active
selective configuration is required. Existing legacy reconciliation sources
remain distinct; they are not silently merged with this source.

An old run rerun outside the seven-day run-created window is excluded, even
when its latest attempt is recent. Registration is bounded to 1,000 retained
provider sources per repository scope. A refusal must remain visible, not be
converted into an empty successful sample.

## 2. Export The Optional Reporter

On a development machine with the admitted Coordinator package installed:

```sh
ci-coordinator-target-artifacts render-reporter \
  --output .ci-coordinator/measure.py
ci-coordinator-target-artifacts check-reporter \
  --output .ci-coordinator/measure.py
```

The generated Python file uses the standard library only. Consumer jobs do not
check out or install the service. Review and commit this optional artifact
through the repository's normal workflow-change process; the existing four
control artifacts remain unchanged.

## 3. Measure A Command

Wrap the actual check command, preserving its original arguments. For example,
the workflow can supply these environment values through its normal secret
and configuration mechanisms:

```yaml
permissions:
  contents: read
  actions: read
  id-token: write
```

```sh
python .ci-coordinator/measure.py \
  --endpoint "$COORDINATOR_URL" \
  --audience "$COORDINATOR_PLAN_AUDIENCE" \
  --installation-id "$INSTALLATION_ID" \
  --sample-key backend-tests \
  --inputs-digest "$PROTECTED_INPUTS_SHA256" \
  --runner-digest "$RUNNER_CLASS_SHA256" \
  --cache-digest "$CACHE_CLASS_SHA256" \
  -- uv run pytest
```

Set `CI_REPORT_GITHUB_TOKEN` from the job's `github.token`. The runner supplies
the OIDC request URL/token and standard GitHub job environment. Do not print
these credentials or put them in command arguments.

The three digests must describe reviewed, secret-free manifests of protected
workload inputs, runner class and cache conditions. Merely assigning identical
digests does not prove identical workloads. Use the same sample key and reporter
bytes for a pair. The first comparison requires the same REST head but different
attempts; it does not pretend a source-code change is a controlled treatment.

The reporter preserves the check's exit code. Missing credentials, rejected
reports and a 15-second upload timeout lose telemetry, not test results. It
measures waited-child CPU and command-launch/wait elapsed time; service CPU,
setup, upload, detached processes and other steps are outside that scope.
Unsupported counters are unavailable, not zero.

After confirmed storage, the job output contains one JSON receipt with
`reportId` and `reportDigest`. Keep the two report IDs for the next step. No
receipt means no confirmed report. Reports expire with their source, 90 days
after run creation; new reports cannot extend that time.

## 4. Compare And Evaluate A Budget

```sh
scope="$COORDINATOR_URL/api/v2/economics/repositories/$INSTALLATION_ID/$REPOSITORY_ID"
curl --fail-with-body --silent --show-error --get \
  --header "Authorization: Bearer $CONTROL_PLANE_ACCESS_TOKEN" \
  --data-urlencode "baselineReportId=$BASELINE_REPORT_ID" \
  --data-urlencode "treatmentReportId=$TREATMENT_REPORT_ID" \
  "$scope/report-comparisons"

curl --fail-with-body --silent --show-error --get \
  --header "Authorization: Bearer $CONTROL_PLANE_ACCESS_TOKEN" \
  --data-urlencode 'counter=elapsed' --data-urlencode 'maximumUs=300000000' \
  "$scope/reports/$TREATMENT_REPORT_ID/budget"
```

Read one retained report at `GET /reports/{reportId}` under that same scope.
Read provider measurements at
`GET /attempts/{runId}/{runAttempt}/measurements?headSha={exactRestHead}`.

Empty `mismatches` allows descriptive counter differences. Positive reduction
means lower measured cost; negative means higher cost. Missing counters and a
zero baseline do not become a fabricated percentage. `coverageStatus` remains
`not_verified` and `causalStatus` remains `not_established` until separately
owned qualification proves more.

The example budget is 300 seconds of measured command elapsed time. It is a
caller-supplied threshold over one report, not a persistent alert subscription
or a promise about total workflow time. Equality is within budget; unavailable
evidence is neither success nor breach. This read never changes CI policy.

## 5. Save A Budget For New Reports

This requires the persistent-budget release and its v3 storage migration;
the source API remains v2. In **CI economics > Budgets**, choose **New policy**,
enter a stable policy key, the report's sample key and producer SHA-256, a
counter and its microsecond maximum. A blank runner-class digest matches any
declared class; it does not assert that runners are equivalent. Save requires
`configure`. The selector's producer digest is available in the retained report.

Each newly accepted matching report receives a signal under that policy
revision. **Signals** shows the historical revision, counter, threshold,
command exit and retention. Equality is within budget; missing counters are
insufficient evidence. No old report is rescanned when a policy changes.
Use the one-report reader above for an explicit historical evaluation.

The equivalent API is:

```text
POST /api/v2/economics/budget-policies
GET  /api/v2/economics/repositories/{installationId}/{repositoryId}/budget-policies
GET  /api/v2/economics/repositories/{installationId}/{repositoryId}/budget-signals
```

The POST body is a closed object. Supply actual scope IDs and a fresh bounded
operation ID; use revision zero to create or the exact current revision to edit:

```json
{
  "installationId": 1,
  "repositoryId": 2,
  "policyKey": "backend-elapsed",
  "expectedRevision": 0,
  "operationId": "create-backend-elapsed-1",
  "configuration": {
    "enabled": true,
    "selector": {
      "sampleKey": "backend-tests",
      "producerDigest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "method": "waited_children/v1",
      "runnerClassDigest": null
    },
    "counter": "elapsed",
    "maximumUs": 300000000
  }
}
```

Replace the example digest with the actual producer digest. Policy reads need
`audit`. Signal reads accept `limit` (1-100), `afterCursor`, `policyKey`, optional
`revision` only with a policy key, and `outcome` (`breached`, `within_budget`,
`insufficient_evidence`). Use a returned cursor unchanged. These are current
pages, not chronological ordering or a complete historical population.

At most 16 identities exist, including disabled policies. Edit an existing
identity instead of deleting/recreating it. Disable by saving `enabled: false`
at its current revision. Confirmed conflicts return HTTP 409. After timeout or
connection loss, read current policies before another explicit change; absence
of a success response does not prove no commit occurred. Browser callers use
the same session/CSRF rules as source registration. Signals expire with their
reports; configuration and its audit history have a separate lifecycle.
These budgets do not send notifications, fail checks or modify CI selection.
