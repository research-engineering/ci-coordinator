# Apply Emergency Validation Controls

Use this procedure only on an authorized deployment. The existing
`POST /api/v1/overrides/full-ci` route can force one subject to FullCI, latch
omission off for a repository, or release that exact latch. It does not rerun
GitHub Actions, cancel a running job, change policy, or create production
authority. Keep the target workflow's independent FullCI fallback available.

## 1. Confirm Scope And Credentials

Record the exact installation ID, repository ID, incident reason, intended
effect and a new operation ID for each logical command. Do not reuse an ID for
a different command. For an incident affecting the whole repository, use
`disable_omission`; a subject-scoped force cannot cover future run attempts.

| Credential | Force or disable | Enable |
| --- | --- | --- |
| Keycloak human session | `override` role | Both `activate` and `override` |
| Allowlisted Keycloak workload access token | `override` role | Both `activate` and `override` |
| Deployment break-glass bearer | Allowed only in its static repository scope allowlist | Forbidden |

Roles never replace repository authorization. Restricted mode uses
`CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST`; App scope mode uses current
App repository access for normal principals instead. Break-glass always uses the
static list, even in App mode, and cannot read the workbench or obtain a browser session.
See [provider onboarding](configure-provider-identity.md) for normal identity.

For the shell examples, have the secret owner supply a private, short-lived
header file containing exactly one `Authorization: Bearer <token>` line.
`/deployment-owned/emergency.headers` holds the break-glass or admitted workload
bearer; `/deployment-owned/administrator.headers` holds an admitted workload
bearer with the enable roles. Keep both outside the checkout and logs; do not
use `curl --verbose`, shell tracing, or a token literal in command history.
These requests must carry no `Cookie` or `Origin` header. Do not follow redirects.

A browser mutation instead uses the existing opaque session, exact configured
`Origin`, and `X-CSRF-Token` from `/api/v1/auth/session`, with no bearer. All
mutations require exactly `Content-Type: application/json`. Never combine the
two credential planes or reuse CSRF proof from a different session.

## 2. Disable Omission For The Repository

Prepare `disable-omission.json` outside the checkout, replacing the example
scope, operation ID and reason with the approved incident facts:

```json
{
  "schemaVersion": "operator-override/v1",
  "kind": "disable_omission",
  "installationId": 101,
  "repositoryId": 202,
  "operationId": "incident-2026-09-25-disable-01",
  "reason": "Investigate untrusted validation evidence"
}
```

Send it to the deployment's canonical HTTPS origin:

```sh
export COORDINATOR_ORIGIN='https://coordinator.example.test'
curl --disable --silent --show-error --fail-with-body --include \
  --header @/deployment-owned/emergency.headers \
  --header 'Content-Type: application/json' \
  --data-binary @/deployment-owned/disable-omission.json \
  "${COORDINATOR_ORIGIN}/api/v1/overrides/full-ci"
```

Expect `202` with `ok:true`, an `overrideId`, and `duplicate:false`; retain the
returned ID with the exact request and incident record. The latch has no expiry
and survives restarts. Do not send `subjectId`, `overrideId` or `expiresAt`,
including `null`. A different disable while already latched conflicts.

## 3. Force One Exact Subject Instead

Use an admitted `read` principal to inspect
`GET /api/v1/workbench/repositories/{installation_id}/{repository_id}`.
Select `runs[].subjectId` only after comparing event, ref, base/head SHA,
workflow run ID and run attempt with the incident. This is the coordinator's
canonical reconciliation identity, not a GitHub run number, PR number or SHA.
The snapshot is bounded: a truncated or absent row does not prove absence.
If the subject cannot be established, do not guess; decide whether the broader
repository disable is authorized instead.

Prepare `force-full-ci.json`; replace the subject and expiry placeholders with
the verified ID and an explicit future timezone-aware time, for example
`2026-09-25T12:05:00Z` only if still appropriate when sent:

```json
{
  "schemaVersion": "operator-override/v1",
  "kind": "force_full_ci",
  "installationId": 101,
  "repositoryId": 202,
  "operationId": "incident-2026-09-25-force-01",
  "reason": "Require complete validation for this exact attempt",
  "subjectId": "<verified runs[].subjectId>",
  "expiresAt": "<future ISO-8601 timestamp with timezone>"
}
```

Use the same curl command with `--data-binary @/deployment-owned/force-full-ci.json`.
An accepted request proves storage, not that an arbitrary supplied subject
matches a real run. Confirm the resulting control against that exact subject.
Force expires without releasing a repository disable; there is no force-delete
command on this route. A duplicate received after expiry does not extend it.

## 4. Release Only The Intended Disable

After the incident is resolved, inspect the latest retained disable and its
audit evidence with an authorized normal principal. Prepare `enable-omission.json`
using its exact returned `overrideId`, not a subject ID or a reconstructed hash:

```json
{
  "schemaVersion": "operator-override/v1",
  "kind": "enable_omission",
  "installationId": 101,
  "repositoryId": 202,
  "operationId": "incident-2026-09-25-enable-01",
  "reason": "Incident resolved and exact disable reviewed",
  "overrideId": "<exact retained disable overrideId>"
}
```

```sh
curl --disable --silent --show-error --fail-with-body --include \
  --header @/deployment-owned/administrator.headers \
  --header 'Content-Type: application/json' \
  --data-binary @/deployment-owned/enable-omission.json \
  "${COORDINATOR_ORIGIN}/api/v1/overrides/full-ci"
```

Omit `subjectId` and `expiresAt`. A wrong/stale disable ID conflicts; break-glass
is forbidden. Releasing the latch does not remove an active subject force or
bypass production admission. To restore the repository safety latch later,
submit a new disable command with a new operation ID. Never delete audit data.

## 5. Interpret Outcomes And Preserve Evidence

| Outcome | Meaning and action |
| --- | --- |
| `202`, `duplicate:false` | State and its audit event were retained atomically; retain `overrideId` and inspect the scoped result. |
| `202`, `duplicate:true` | Exact command already retained; original identity and application time are unchanged. |
| `400 invalid_override` | Invalid domain shape or expired new force; review facts before a new logical command. |
| `401 unauthenticated` | Credential missing, invalid, expired or ambiguous; no automatic resubmission after sign-in. |
| `403 forbidden` | Role, scope or request integrity denied; do not substitute a broader credential silently. |
| `409 conflict` | Divergent operation replay or invalid latch transition; inspect authoritative state. |
| `422` / `413` | Invalid DTO / oversized body; use the exact [HTTP contract](../reference/http-surface.md#operator-override-requests). |
| `503`, timeout or lost response | Do not assume no commit. Preserve the exact command and inspect state before a deliberate retry. |

An authorized retry must preserve scope, operation ID, kind, reason, subject or
disable ID, expiry, and the same server-derived actor. Re-authentication is not
permission to invent a new operation for an uncertain result. An exact replay
returns the original record; changed facts conflict. Revalidate credentials
and roles before retrying; no automatic retry is part of this procedure.

Retain redacted request/response facts, actor, incident approval, scope,
override/audit identities, and follow-up FullCI evidence, never credentials.
For emergency credential rotation or service rollback use the
[deployment guide](deploy-container.md#8-rotate-or-revoke-the-break-glass-bearer).
The [operator-controls owner](../architecture/modules/operator-controls.md)
defines the durable transition and replay guarantees.
