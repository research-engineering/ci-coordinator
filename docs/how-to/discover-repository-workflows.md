# Discover Repository Workflows

Use this procedure to inspect one authorized repository at an immutable commit
and obtain either an admitted observe-only policy proposal or explicit blocking
evidence.

## Preconditions

- CI Coordinator is running in a connected mode with a configured GitHub App.
- For browser use, the signed-in Keycloak principal has the `read` role; for
  API use, a Keycloak workload token has the same role.
- The requested installation and repository belong to the configured
  control-plane allowlists.
- The GitHub App installation has read access to repository contents.
- For API use, a short-lived access token for the exact API audience is
  available outside browser code.

Provider catalog visibility alone is insufficient. Every repository read stays
within the immutable repository id and exact App installation relation;
Keycloak role admission cannot widen the deployment scope.

## Use The Workbench

1. Open `/workbench` on the reported or deployed UI origin.
2. Select an active organization installation.
3. Search the repository catalog and select `Open` for the target repository.
4. Select **Workflows** in the repository sidebar.
5. Wait for **Workflow discovery** to resolve the current default-branch head.
6. Confirm the displayed 40-character revision before interpreting evidence.
7. Inspect **Workflow topology**, **Explicit unknowns**, and **Proven assertions**.
8. Inspect **Repository policy proposal**.

To inspect a historical snapshot, enter an exact lowercase 40-hex commit SHA in
**Revision** and select **Scan**. Historical scans are inventory-only: their
proposal is blocked with `default_branch_head_unproven`, because that commit
cannot prove the workflow still exists on the current default branch. Branch
names, tags, abbreviated SHAs, and uppercase object ids are rejected before the
discovery request.

## Interpret The Result

| Signal                        | Meaning                                                                                                                                              |
|-------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| `complete = true`             | Every admitted source terminated as one parsed workflow; it does not mean runtime behavior is proven.                                                |
| `localGraphClosed = true`     | Every local reusable-workflow call resolved in the same snapshot without a cycle or depth violation.                                                 |
| Safety unknown                | A required effective or dynamic fact is not proven and cannot authorize reduced validation.                                                          |
| `proposal.state = reviewable` | The observe-only source passed existing policy admission; it is not reviewed, registered, or active until the separate affirmative command succeeds. |
| `proposal.state = blocked`    | No policy source exists; inspect blockers and diagnostics.                                                                                           |

A reviewable proposal also identifies the selected CI events and one stable
provider signal. For a multi-job workflow, that signal is an admitted
`always()` job transitively downstream of every job in a complete acyclic
`needs` graph. Each event must cover the complete coordinator action domain on
the default branch without path or exclusion filters, and no overlapping
workflow may publish a colliding provider signal. This proves static terminal
topology and signal availability, not FullCI step semantics.

Secret names are metadata. Secret values are never fetched or returned.

## Use The API

Scan the current default-branch head:

```sh
curl --fail-with-body \
  --header "Authorization: Bearer ${CONTROL_PLANE_ACCESS_TOKEN}" \
  --header "Accept: application/json" \
  "${CI_COORDINATOR_URL}/api/v1/workbench/repositories/${INSTALLATION_ID}/${REPOSITORY_ID}/workflow-discovery"
```

Inspect one exact historical commit without proposal eligibility:

```sh
curl --fail-with-body \
  --header "Authorization: Bearer ${CONTROL_PLANE_ACCESS_TOKEN}" \
  --header "Accept: application/json" \
  "${CI_COORDINATOR_URL}/api/v1/workbench/repositories/${INSTALLATION_ID}/${REPOSITORY_ID}/workflow-discovery?revision=${COMMIT_SHA}"
```

Treat the response as request-scoped evidence. A request without `revision` may
resolve a newer head on the next call. The current slice has no scan id,
durable scan history, or discovery CLI. To register an exact current-head
proposal as reviewed without activation, follow
[Review A Workflow Proposal](review-workflow-proposal.md).

## Failure Handling

- `401`: establish a Keycloak session or workload identity; do not place the token in frontend
  source or browser storage.
- `403`: restore the required role or configured repository scope; catalog
  visibility is not enough.
- `404`: verify repository installation access and exact commit existence.
- `422`: provide an exact lowercase 40-hex commit SHA.
- `429`: retry after the provider limit clears; do not substitute stale data.
- `503`: inspect the typed error and retry only after provider, identity, tree,
  blob, or resource-bound evidence is trustworthy.

Do not register policy from copied bytes. Use the governed review command so
the service reproduces current-head evidence and the active baseline. Review is
still non-activating; activation requires a separate authority.
