# Replay Audit Evidence

Status: as-built CLI how-to

Last verified: 2026-07-16

## Outcome

Verify the complete PostgreSQL audit chain and then project all records, one
subject, or one event. This is the installed CI Coordinator audit replay CLI.

```text
ReportedValid(filter) => CompleteLedgerValid and FilterProjectionValid
```

Replay is read-only and performs no GitHub request. See
[Audit Replay](../architecture/modules/audit-replay.md) for the owned contract.

## Prepare

Install the locked environment, then supply the DSN through the environment so
it does not appear in the process argument list:

```sh
uv sync --project backend --frozen --all-groups
export CI_COORDINATOR_DATABASE_DSN='postgresql+psycopg://...'
```

The database must already contain the admitted schema and runtime principal
grants.

## Replay

Replay the complete ledger:

```sh
uv run --project backend --frozen ci-coordinator-audit-replay --all
```

Replay one known subject:

```sh
uv run --project backend --frozen ci-coordinator-audit-replay \
  --subject 'dynamic-ci-plan:<plan-id>'
```

Replay one event:

```sh
uv run --project backend --frozen ci-coordinator-audit-replay \
  --audit-event-id 'audit_0123456789abcdef0123456789abcdef'
```

Exactly one filter is required. Unknown options, malformed ids, conflicting
filters, or missing database configuration produce one redacted error.

Use `--include-payload` only in an authorized diagnostic session:

```sh
uv run --project backend --frozen ci-coordinator-audit-replay \
  --subject 'dynamic-ci-plan:<plan-id>' --include-payload
```

Payloads can contain operational evidence that should not enter tickets or
unrestricted logs.

## Interpret the Exit Status

| Status | Meaning                                  | Action                                                  |
|-------:|------------------------------------------|---------------------------------------------------------|
|      0 | Chain and selected projection are valid. | Retain the JSON receipt.                                |
|      2 | Configuration or storage failed.         | Restore dependency availability; do not infer validity. |
|      3 | No record matched.                       | Verify the subject or event identity.                   |
|      4 | Ledger or replay result is invalid.      | Block admission and investigate.                        |

Filtering cannot hide an earlier corrupt record because full-chain validation
precedes projection. For a non-empty valid result, the CLI writes the second
verified pass to an owner-private temporary file and publishes it in fixed-size
chunks only after the complete JSON document closes. This keeps memory bounded
by one replay page plus one output chunk. Temporary storage is proportional to
the selected output, so capacity qualification must include its largest
authorized replay.

Treat output as a receipt only when the process exits `0`, the complete stdout
parses as one JSON value, and its terminal fields are `ok=true` and
`status=valid`. Discard every stdout prefix from a non-zero run; an output-sink
failure can occur after publication begins and cannot be rolled back.
