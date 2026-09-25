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

Have the secret owner provision a file outside the checkout containing exactly
one `Authorization: Bearer <workload-access-token>` line for the intended actor.
The operator must own it with mode `0600` in a mode `0700` directory. Do not
paste the token into shell commands, export it, print the file, or enable shell
or curl tracing. Do not mix browser cookies or other authorization fields with
this workload credential. File permissions do not isolate secrets from the
same host account or privileged operators.

Set only non-secret shell values:

```sh
export COORDINATOR_URL=https://ci-coordinator.example.com
export CONTROL_PLANE_HEADER_FILE=/deployment-owned/control-plane-authorization.header
export INSTALLATION_ID=100
export REPOSITORY_ID=200
export PROPOSAL_MANIFEST_ID='proposal:replace-with-32-lowercase-hex-characters'
export REGISTRATION_OPERATION_ID='register-policy-001'
```

The commands use [`curl --header @file`](https://curl.se/docs/manpage.html#-H),
with `--disable` first to ignore ambient curl configuration. They do not follow
redirects or retry automatically. Do not place credentials in policy source.

Create unique private storage once for this procedure, in an operator-controlled
temporary location outside the checkout. Keep this shell's restrictive umask
for every command below; directories are `0700`, new files are `0600`:

```sh
umask 077
policy_dir="$(mktemp -d "${TMPDIR:-/tmp}/ci-policy.XXXXXXXX")"
```

Stop if any step fails. Retain the request and response files, including failed
or uncertain responses, in the authorized change record. Do not rerun setup or
overwrite receipts to retry: first reconcile the outcome, then retain the same
actor, source, scope and operation id for an exact replay in fresh private
storage. No automatic cleanup is performed; remove only this exact directory
after reconciliation and the owner's retention decision.

## Validate Source Without Effects

Build a validation envelope without shell-interpolating YAML:

```sh
jq -n \
  --rawfile source config.example.yaml \
  '{
    schemaVersion: "ci-config-epoch-validation/v1",
    sourceFormat: "yaml-1.2",
    source: $source
  }' > "${policy_dir:?}/validation.request.json" &&
curl --disable --fail-with-body --silent --show-error \
  --request POST \
  --header "@${CONTROL_PLANE_HEADER_FILE}" \
  --header 'Content-Type: application/json' \
  --data "@${policy_dir}/validation.request.json" \
  --output "${policy_dir}/validation.response.json" \
  "${COORDINATOR_URL}/api/v1/config/validations" &&
jq . "${policy_dir}/validation.response.json"
```

A successful response has schema
`ci-config-epoch-validation-result/v1`. Validation runs the same bounded policy
admission used by registration but creates no epoch, audit event, receipt, or
provider effect.

## Register an Epoch

Build the JSON envelope without shell-interpolating YAML, register and retain
the receipt. Clear any previous epoch value before admitting a new response:

```sh
unset epoch_id
jq -n \
  --arg operationId "${REGISTRATION_OPERATION_ID}" \
  --rawfile source config.example.yaml \
  '{
    schemaVersion: "ci-config-epoch-registration/v1",
    sourceFormat: "yaml-1.2",
    source: $source,
    operationId: $operationId
  }' > "${policy_dir:?}/registration.request.json" &&
curl --disable --fail-with-body --silent --show-error \
  --request POST \
  --header "@${CONTROL_PLANE_HEADER_FILE}" \
  --header 'Content-Type: application/json' \
  --data "@${policy_dir}/registration.request.json" \
  --output "${policy_dir}/registration.response.json" \
  "${COORDINATOR_URL}/api/v1/config/epochs" &&
jq . "${policy_dir}/registration.response.json" &&
epoch_id="$(jq -er '.epochId' "${policy_dir}/registration.response.json")"
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

Set the independently retained target and baseline. Use JSON `null` only for a
reviewed baseline with no active configuration; otherwise use its exact integer
revision. Use a stable operation id for retries of the same logical command:

```sh
reviewed_epoch_id='replace-with-target-epoch-from-the-same-reviewed-proposal'
expected_revision=null
operation_id='activate-policy-001'
```

Invoke this whole block. The subshell contains the guard, body construction
and request, so a mismatch sends nothing and cannot exit the parent shell.
The conditional call also keeps a parent using `set -e` alive on failure:

```sh
activate_reviewed_policy() (
  test "${epoch_id:?}" = "${reviewed_epoch_id:?}" || {
    printf '%s\n' 'Epoch mismatch; no activation sent.' >&2
    exit 1
  }
  umask 077
  jq -n \
    --arg epochId "${epoch_id}" \
    --arg operationId "${operation_id:?}" \
    --arg proposalManifestId "${PROPOSAL_MANIFEST_ID:?}" \
    --argjson installationId "${INSTALLATION_ID:?}" \
    --argjson repositoryId "${REPOSITORY_ID:?}" \
    --argjson expectedRevision "${expected_revision:?}" \
    '{
      schemaVersion: "ci-config-epoch-activation/v1",
      installationId: $installationId,
      repositoryId: $repositoryId,
      targetEpochId: $epochId,
      proposalManifestId: $proposalManifestId,
      expectedRevision: $expectedRevision,
      operationId: $operationId
    }' > "${policy_dir:?}/activation.request.json" || exit 1

  curl --disable --fail-with-body --silent --show-error \
    --request POST \
    --header "@${CONTROL_PLANE_HEADER_FILE:?}" \
    --header 'Content-Type: application/json' \
    --data "@${policy_dir}/activation.request.json" \
    --output "${policy_dir}/activation.response.json" \
    "${COORDINATOR_URL:?}/api/v1/config/activations" || exit 1
  jq -er '.revision' "${policy_dir}/activation.response.json"
)

if active_revision="$(activate_reviewed_policy)"; then
  printf 'Active revision: %s\n' "${active_revision}"
else
  unset active_revision
  printf '%s\n' 'No admitted activation receipt. Stop and reconcile before retrying.' >&2
fi
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
  --argjson expectedRevision "${active_revision:?}" \
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
  }' > "${policy_dir:?}/rollback.request.json" &&
curl --disable --fail-with-body --silent --show-error \
  --request POST \
  --header "@${CONTROL_PLANE_HEADER_FILE}" \
  --header 'Content-Type: application/json' \
  --data "@${policy_dir}/rollback.request.json" \
  --output "${policy_dir}/rollback.response.json" \
  "${COORDINATOR_URL}/api/v1/config/rollbacks" &&
jq . "${policy_dir}/rollback.response.json"
```

Rollback fails closed when target coverage is lower, incomparable, unknown, or
unavailable.

## Inspect Current State

Read the active pointer and one authenticated, repository-scoped epoch page:

```sh
curl --disable --fail-with-body --silent --show-error \
  --header "@${CONTROL_PLANE_HEADER_FILE}" \
  --output "${policy_dir:?}/status.response.json" \
  "${COORDINATOR_URL}/api/v1/config/repositories/${INSTALLATION_ID}/${REPOSITORY_ID}/status?limit=20" &&
jq '{active, epochs, nextCursor}' "${policy_dir}/status.response.json"
```

When `nextCursor` is non-null, request the next page using its exact value:

```sh
after_epoch_id="$(jq -er '.nextCursor' "${policy_dir:?}/status.response.json")" &&
curl --disable --fail-with-body --silent --show-error \
  --get \
  --header "@${CONTROL_PLANE_HEADER_FILE}" \
  --data-urlencode "afterEpochId=${after_epoch_id}" \
  --data-urlencode 'limit=20' \
  --output "${policy_dir}/status-next.response.json" \
  "${COORDINATOR_URL}/api/v1/config/repositories/${INSTALLATION_ID}/${REPOSITORY_ID}/status" &&
jq . "${policy_dir}/status-next.response.json"
```

Pages are keyset-ordered by immutable epoch id. They are bounded live reads,
not a snapshot across concurrent registrations. Restart traversal when a
stable collection view matters and concurrent registrations may have occurred.
The active pointer is the sole owner of activation revision.

## Export Exact Retained Source

Export one epoch only through its repository scope and retain response headers:

```sh
curl --disable --fail-with-body --silent --show-error \
  --dump-header "${policy_dir:?}/source.headers" \
  --output "${policy_dir}/source.yaml" \
  --header "@${CONTROL_PLANE_HEADER_FILE}" \
  "${COORDINATOR_URL}/api/v1/config/repositories/${INSTALLATION_ID}/${REPOSITORY_ID}/epochs/${epoch_id}/source" &&
cat "${policy_dir}/source.headers"
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
