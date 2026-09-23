# Manage Repository Policy Epochs

Status: as-built HTTP how-to

Last verified: 2026-09-04

## Outcome

Validate repository policy source without effects, register it idempotently,
activate it with compare-and-swap semantics, inspect bounded state, export the
exact retained source, and roll back to a retained equal-or-stronger epoch
without restarting the service.

Validation, registration, activation, and rollback are distinct operations:

```text
Validate(source) creates no durable or provider effect
Registered(epoch) does not imply Active(epoch)
ActiveTransition requires AuthorizedScope and ExactExpectedRevision
```

The behavior contract is owned by
[Config Epoch Lifecycle](../architecture/modules/config-epoch-lifecycle.md).

## Preconditions

- an already provisioned connected instance in `non_enforcing` or `enforcing` mode;
- a short-lived Keycloak workload access token with the exact API audience and
  `configure` and `activate` roles;
- `jq` and `curl`;
- an admitted secret-free policy source, such as `config.example.yaml` after
  replacing repository identities with real values.

Set shell values without committing credentials:

```sh
export COORDINATOR_URL=https://ci-coordinator.example.com
export CONTROL_PLANE_ACCESS_TOKEN='replace-with-short-lived-keycloak-access-token'
export INSTALLATION_ID=100
export REPOSITORY_ID=200
export PROPOSAL_MANIFEST_ID='proposal:replace-with-32-lowercase-hex-characters'
export REGISTRATION_OPERATION_ID='register-policy-001'
```

Send exactly one `Authorization` field. Do not place credentials in the policy
document.

## Validate Source Without Effects

Build a validation envelope without shell-interpolating YAML:

```sh
jq -n \
  --rawfile source config.example.yaml \
  '{
    schemaVersion: "ci-config-epoch-validation/v1",
    sourceFormat: "yaml-1.2",
    source: $source
  }' > /tmp/ci-config-validation.json

curl --fail-with-body --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${CONTROL_PLANE_ACCESS_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data @/tmp/ci-config-validation.json \
  "${COORDINATOR_URL}/api/v1/config/validations" | jq .
```

A successful response has schema
`ci-config-epoch-validation-result/v1`. Validation runs the same bounded policy
admission used by registration but creates no epoch, audit event, receipt, or
provider effect.

## Register an Epoch

Build the JSON envelope without shell-interpolating YAML:

```sh
jq -n \
  --arg operationId "${REGISTRATION_OPERATION_ID}" \
  --rawfile source config.example.yaml \
  '{
    schemaVersion: "ci-config-epoch-registration/v1",
    sourceFormat: "yaml-1.2",
    source: $source,
    operationId: $operationId
  }' > /tmp/ci-config-registration.json
```

Register and retain the receipt:

```sh
registration="$(
  curl --fail-with-body --silent --show-error \
    --request POST \
    --header "Authorization: Bearer ${CONTROL_PLANE_ACCESS_TOKEN}" \
    --header 'Content-Type: application/json' \
    --data @/tmp/ci-config-registration.json \
    "${COORDINATOR_URL}/api/v1/config/epochs"
)"

printf '%s\n' "${registration}" | jq .
epoch_id="$(printf '%s' "${registration}" | jq -er '.epochId')"
```

The first exact registration returns HTTP 201 with schema
`ci-config-epoch-registration-result/v1`. An exact replay with the same scoped
operation id and all the same client facts returns HTTP 200 with
`duplicate: true` and writes nothing. Reusing that operation id with changed
source, format, or actor is a conflict. A validation failure returns typed
diagnostics and creates no epoch, receipt, or audit event.

## Activate the Epoch

Activation requires the exact durable repository review for the proposal
manifest. Complete [Review And Activate A Workflow Proposal](review-workflow-proposal.md)
through the review step before using the API. Retain the displayed manifest,
target epoch and active baseline from that same current-head proposal.
`PROPOSAL_MANIFEST_ID` must identify that exact retained review. The epoch
registered above must equal its target epoch: registration of an unrelated
`config.example.yaml` does not make a proposal activatable. If they differ,
stop and register the exact reviewed policy; do not substitute identities.

Before constructing the activation body, verify the retained target:

```sh
reviewed_epoch_id='replace-with-target-epoch-from-the-same-reviewed-proposal'
test "${epoch_id}" = "${reviewed_epoch_id}" || exit 1
```

For a reviewed baseline with no active configuration, use
`expectedRevision: null`. Otherwise use its exact retained revision. Use a
stable operation id for retries of the same logical command.

```sh
operation_id='activate-policy-001'

jq -n \
  --arg epochId "${epoch_id}" \
  --arg operationId "${operation_id}" \
  --arg proposalManifestId "${PROPOSAL_MANIFEST_ID}" \
  --argjson installationId "${INSTALLATION_ID}" \
  --argjson repositoryId "${REPOSITORY_ID}" \
  '{
    schemaVersion: "ci-config-epoch-activation/v1",
    installationId: $installationId,
    repositoryId: $repositoryId,
    targetEpochId: $epochId,
    proposalManifestId: $proposalManifestId,
    expectedRevision: null,
    operationId: $operationId
  }' > /tmp/ci-config-activation.json

activation="$(
  curl --fail-with-body --silent --show-error \
    --request POST \
    --header "Authorization: Bearer ${CONTROL_PLANE_ACCESS_TOKEN}" \
    --header 'Content-Type: application/json' \
    --data @/tmp/ci-config-activation.json \
    "${COORDINATOR_URL}/api/v1/config/activations"
)"

printf '%s\n' "${activation}" | jq .
active_revision="$(printf '%s' "${activation}" | jq -er '.revision')"
```

For a later transition, replace `null` with the exact retained current
revision. A `revision_conflict` is a stop signal: reload the repository
configuration status and compare its active epoch with the independently
retained command receipt. Do not guess a revision. A successful activation or
rollback response uses `ci-config-epoch-activation-result/v1`.

## Roll Back

Retain the prior epoch id before a later activation. Rollback requires that id,
the exact current revision, a new operation id, and an audit reason.

```sh
export PREVIOUS_EPOCH_ID='replace-with-retained-64-character-epoch-id'
rollback_operation_id='rollback-policy-001'

jq -n \
  --arg epochId "${PREVIOUS_EPOCH_ID}" \
  --arg operationId "${rollback_operation_id}" \
  --arg reason 'Restore the last admitted policy after failed shadow validation.' \
  --argjson expectedRevision "${active_revision}" \
  --argjson installationId "${INSTALLATION_ID}" \
  --argjson repositoryId "${REPOSITORY_ID}" \
  '{
    schemaVersion: "ci-config-epoch-rollback/v1",
    installationId: $installationId,
    repositoryId: $repositoryId,
    targetEpochId: $epochId,
    expectedRevision: $expectedRevision,
    operationId: $operationId,
    reason: $reason
  }' > /tmp/ci-config-rollback.json

curl --fail-with-body --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${CONTROL_PLANE_ACCESS_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data @/tmp/ci-config-rollback.json \
  "${COORDINATOR_URL}/api/v1/config/rollbacks" | jq .
```

Rollback fails closed when target coverage is lower, incomparable, unknown, or
unavailable.

## Inspect Current State

Read the active pointer and one authenticated, repository-scoped epoch page:

```sh
curl --fail-with-body --silent --show-error \
  --header "Authorization: Bearer ${CONTROL_PLANE_ACCESS_TOKEN}" \
  "${COORDINATOR_URL}/api/v1/config/repositories/${INSTALLATION_ID}/${REPOSITORY_ID}/status?limit=20" \
  | tee /tmp/ci-config-status.json \
  | jq '{active, epochs, nextCursor}'
```

When `nextCursor` is non-null, request the next page using its exact value:

```sh
after_epoch_id="$(jq -er '.nextCursor' /tmp/ci-config-status.json)"

curl --fail-with-body --silent --show-error \
  --get \
  --header "Authorization: Bearer ${CONTROL_PLANE_ACCESS_TOKEN}" \
  --data-urlencode "afterEpochId=${after_epoch_id}" \
  --data-urlencode 'limit=20' \
  "${COORDINATOR_URL}/api/v1/config/repositories/${INSTALLATION_ID}/${REPOSITORY_ID}/status" \
  | jq .
```

Pages are keyset-ordered by immutable epoch id. They are bounded live reads,
not a snapshot across concurrent registrations. Restart traversal when a
stable collection view matters and concurrent registrations may have occurred.
The active pointer is the sole owner of activation revision.

## Export Exact Retained Source

Export one epoch only through its repository scope and retain response headers:

```sh
curl --fail-with-body --silent --show-error \
  --dump-header /tmp/ci-config-source.headers \
  --output /tmp/ci-config-source.yaml \
  --header "Authorization: Bearer ${CONTROL_PLANE_ACCESS_TOKEN}" \
  "${COORDINATOR_URL}/api/v1/config/repositories/${INSTALLATION_ID}/${REPOSITORY_ID}/epochs/${epoch_id}/source"

cat /tmp/ci-config-source.headers
```

The body is the exact retained and re-admitted source. `Content-Digest` is the
RFC 9530 raw SHA-256 digest of those response bytes. `ETag` is the quoted,
domain-separated source hash and is deliberately not interchangeable with the
raw body digest. The response is `private, no-store`; treat the exported source
as operator-controlled configuration even though admitted policy forbids
secrets.

The broader workbench remains useful for bounded config and audit context from
one read-only repeatable snapshot. Preserve mutation receipts in the authorized
change record and use the [audit replay CLI](replay-audit-evidence.md) when
complete ledger verification is required. Neither status nor the workbench may
be represented as complete audit history.
