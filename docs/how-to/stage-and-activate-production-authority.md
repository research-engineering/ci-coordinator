# Stage And Activate Production Authority

Status: candidate HTTP procedure; native and deployment qualification pending

## Outcome

Replace one repository's selected-execution authority without allowing old and
new generations to overlap. Staging is not activation. Beginning a cutover
forces FullCI; only a successful activation releases that cutover's latch.

The [generation-fenced authority design](../features/generation-fenced-production-authority.md)
owns the contract. This procedure neither provisions an instance nor authorizes
a deployment, GitHub App change, or live webhook request.

## Preconditions

- A qualified `enforcing` instance with Keycloak control-plane identity and the
  successor database capabilities. Startup registration alone activates nothing.
- An active repository configuration and a signed v2 production receipt for
  its exact scope, next generation, target workflow relation, and evidence.
- The complete canonical evidence bundle, its exact provider path inventory,
  and an authorized independent owner able to attest the external drain facts.
- A short-lived workload token with the exact API audience, scope permission,
  and the operation-specific roles below. Browser sessions additionally use
  the configured CSRF and origin admission; a static operator token is not a
  substitute for these production administration permissions.

Retain tokens outside version control. The examples below describe request
construction only; they must not be run against an unapproved deployment.

## Inspect Before Writing

Read `GET /api/v1/production/scopes/{installationId}/{repositoryId}` with a
`read` role. Retain its complete `state`, especially `revision`, `generation`,
`stagedAuthorityId`, and `latchOverrideId`. An absent scope returns `not_found`;
its first stage uses `expectedRevision: 0` and successor generation one. Do not
infer production readiness from this read.

Every command uses this common body:

```json
{
  "schemaVersion": "ci-coordinator.production-cutover-request/v1",
  "installationId": 100,
  "repositoryId": 200,
  "operationId": "replace-with-one-logical-command-id",
  "authorityId": "production_admission_00000000000000000000000000000000",
  "expectedRevision": 0,
  "reason": "Replace with the authorized change reason."
}
```

Replace the illustrative identities with the actual receipt and inspected
revision. A command is identified by its scope, operation kind, operation id,
actor, reason, expected revision, authority, and exact input digest. Use a
different operation id for a different logical command.

## Stage, Drain, Activate

| Step     | Endpoint                              | Role        | Additional fields or evidence           |
|----------|---------------------------------------|-------------|-----------------------------------------|
| Stage    | `POST /api/v1/production/stages`      | `configure` | `envelope`, `evidence`, `providerPaths` |
| Begin    | `POST /api/v1/production/cutovers`    | `override`  | Common command body only                |
| Activate | `POST /api/v1/production/activations` | `activate`  | `drainEnvelope`                         |

For stage, `envelope` and `evidence` are UTF-8 strings containing the exact
canonical signed receipt and evidence bytes, including their final newline.
`providerPaths` is the complete admitted array of workflow paths, not a sample.
Use a structured JSON encoder, such as `jq --rawfile`, rather than shell
interpolation. The complete HTTP body, including escaping and metadata, must
fit within 8 MiB; a bundle that fits alone may still exceed that request bound.

After each successful command, retain its receipt and use the returned revision
for the next command. Stage checks active configuration and replays the supplied
provider evidence before retaining it; fresh provider observation is a separate
activation and request-time obligation. Stage does not enable selected execution. Begin
revokes the predecessor generation and installs a durable FullCI latch.

After begin, the independent drain owner must establish all three facts:

1. Old coordinator replicas cannot receive new relevant requests.
2. Their previously admitted requests have completed.
3. Predecessor target executions have ended.

The owner signs a drain statement bound to the exact scope, authority, subject,
successor and predecessor generations, current revision, and latch. Its
observation cannot precede latch application; its validity interval is at most
300 seconds. A boolean assertion without the owner signature is not a receipt.
The API does not produce or independently observe these external facts.

Submit that canonical signed envelope as `drainEnvelope`. Activation also checks
local reconciliation state, retained leases, unexpired signed plans, fresh
provider evidence, and active configuration under the generation fence.
A terminal reconciliation result alone does not prove that its signed plan has
expired or that a target execution has ended.

## Handle Retries And Rejections

- After an uncertain response, replay the exact command with the same actor,
  operation id, reason, revision, and bytes. An admitted duplicate returns its
  historical result and writes nothing; it is not a new freshness attestation.
- On `revision_changed`, `stage_changed`, or `generation_changed`, inspect state
  and reconcile the authorized change before constructing a new command. Never
  replace the expected revision automatically until something succeeds.
- On `drain_incomplete`, leave the latch in place and establish the missing
  premises. A refreshed signed envelope is a new command, not an exact replay.
- On stale authority, changed configuration, invalid current evidence, or
  unavailable dependencies, retain FullCI and obtain newly admissible evidence.
- On `capacity_exhausted`, do not delete retained evidence or bypass limits.
  Escalate to the retention/capacity owner; this API performs no eviction.

An ordinary enable override cannot clear a production cutover latch. A failed
activation must not be worked around by clearing rows or reusing an old grant.

## Verify The Result

Inspect the scope again. A successful new activation advances the generation,
sets the exact active authority and subject, and clears the matching latch.
Read the retained canonical evidence through
`GET /api/v1/production/scopes/{installationId}/{repositoryId}/evidence/{authorityId}`.
These authenticated reads are scoped and marked `no-store`.

Selected execution still requires request-time evidence and durable checks at
registration and plan issuance. Unknown or stale evidence yields FullCI.
API success is not proof of CPU savings, working webhook delivery, target
execution, or production capacity; obtain those receipts through the qualified
pilot and deployment procedures.
