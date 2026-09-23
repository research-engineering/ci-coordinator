# GitHub Ingestion Module Specification

Status: module specification

Date: 2026-07-13

## 1. Owned Invariant

An already signature-admitted GitHub delivery becomes one immutable, bounded,
provenance-bound domain result. It never becomes a plan, a policy decision, a
repository mutation, or an implicit empty event.

`TrustedWebhook` proves only the HMAC admission performed by
`identity_admission`. Its `body_sha256` must still bind the exact bytes consumed
by this module; webhook JSON remains untrusted data until it is bounded, parsed,
and normalized.

## 2. Public API

```text
prepare_trusted_webhook_ingestion(trusted_webhook, raw_body, profile)
  -> PreparedWebhookIngestion | IngestionRejection

complete_webhook_ingestion(prepared, delivery_port)
  -> SeedIngestion | WorkflowRunObservation | WorkflowJobObservation | NoopDelivery | PingDelivery
     | UnsupportedWebhook | DuplicateDelivery | DeliveryUnavailable
     | IngestionRejection
```

The public entry point accepts neither HTTP headers nor a webhook secret.
Signature admission belongs exclusively to `identity_admission`; raw HTTP body
collection, header parsing, and HTTP response mapping belong exclusively to the
HTTP ingress boundary.

The current runtime persists the delivery claim and may offer an admitted
seed to the process-local preparation worker. HTTP reports
`downstream: best_effort_preparation` for an accepted offer and
`downstream: none` otherwise, as required by `REQ-CI-RUNTIME-041`. An offer
does not prove completed preparation or survive a process restart. There is
no durable seed inbox; acknowledgement as accepted durable queued work
requires an atomic inbox before later processing can be promised.

## 3. Sequencing Law

```text
RawBodyMatchesProvenance
and PayloadFitsLimits
and StrictJsonObject(raw_body)
and DeliveryClaimed
=> ExactlyOneNormalizedDeliveryResult
```

The converse does not hold: a normalized result does not authorize planning or
any GitHub effect.

The execution order is fixed:

```text
body-hash binding
  -> byte and structural preflight
  -> strict JSON parse and immutable freeze
  -> event-family binding and normalization
     (repository extraction is required except for the no-authority ping)
  -> immutable preparation result
  -> idempotency claim
  -> seed, observation, no-op, unsupported, or rejection result
```

Therefore malformed or oversized bytes never reserve a delivery key, and a
duplicate key never emits a second seed or observation. Expected delivery-store
unavailability is a typed result. Cancellation and unexpected programming
errors propagate; catching them as a rejection would falsely represent an
unfinished operation as a completed domain decision.

## 4. Resource And Representation Law

`webhook-ingestion-profile.v2.json` owns the fixed maximum body bytes, nesting
depth, node count, collection cardinality, string scalar count, supported event
actions, and stable result codes. The limit value is explicit input to parsing;
there is no environment-derived default.

The parser must perform a string-aware structural preflight before `json.loads`
can construct a host object. Duplicate keys, non-finite constants, unsafe JSON
integers, unpaired surrogates, non-object roots, and profile-limit violations
are typed rejections. Rejection messages contain stable codes only and never
copy raw payload values.

## 5. Event Algebra

| Family         | Admitted result                            | Planning consequence                                                     |
|----------------|--------------------------------------------|--------------------------------------------------------------------------|
| `push`         | `PushSeed` or explicit no-op               | candidate planning input only                                            |
| `pull_request` | `PullRequestSeed` or explicit no-op        | candidate planning input only                                            |
| `merge_group`  | `MergeGroupSeed` or explicit no-op         | candidate planning input only                                            |
| `workflow_run` | `WorkflowRunObservation` or explicit no-op | no plan; preserves workflow identity for later reconciliation            |
| `workflow_job` | `WorkflowJobObservation` or explicit no-op | no plan; preserves job identity for observational collection             |
| `ping`         | `PingDelivery`                             | no repository authority, seed, workflow observation or preparation offer |
| other          | `UnsupportedWebhook`                       | no implicit success                                                      |

`merge_group` identity is independent of pull-request identity. A missing merge
group ref produces a FullCI-invalidating range rather than a synthetic ref. An
ambiguous base SHA produces a FullCI-invalidating range. For repository-scoped
CI families, an event action outside the profile is a `NoopDelivery`, not a
planning seed.

`ping` admits the bounded core `zen: string`, positive safe integer `hook_id`,
`hook: object` with the same positive safe integer `id`, and absent/null action.
An invalid ping action or a profile veto rejects before the delivery claim.
Optional repository, installation, sender and hook metadata are inert. A ping
requires no repository or installation identity, still commits a delivery claim,
and returns the existing ignored-webhook HTTP shape. CI-family keys mixed with
ping keys reject; relabelling either family through the unsigned event header
cannot reinterpret the signed body. This core projection does not validate
unused provider fields or prove provider delivery freshness.

## 6. Contract Boundary

The normalized event algebra and the machine-owned webhook profile define the
complete admitted event families. An action narrowing or a new result variant
is a contract evolution and requires a versioned profile plus native boundary
witnesses. Host-language implementation details are not wire authority.

## 7. File Ownership

| File                                                              | Single responsibility                                   |
|-------------------------------------------------------------------|---------------------------------------------------------|
| `events.py`                                                       | immutable normalized-event algebra                      |
| `seeds.py`                                                        | immutable planning-seed and range algebra               |
| `results.py`                                                      | immutable public ingestion-result algebra               |
| `payload_limits.py`                                               | profile-derived immutable parsing limits                |
| `strict_json.py`                                                  | bounded strict JSON preflight and freeze                |
| `provenance.py`                                                   | trusted-webhook to immutable delivery provenance        |
| `event_common.py`                                                 | repository identity and shared scalar extraction        |
| `push.py`, `pull_request.py`, `merge_group.py`, `workflow_run.py` | one event-family normalization each                     |
| `ping.py`                                                         | repository-independent no-authority ping core admission |
| `event_normalizer.py`                                             | closed dispatcher only                                  |
| `seed_builder.py`                                                 | normalized planning seed or explicit no-op              |
| `ports.py`                                                        | typed idempotency port/result algebra                   |
| `use_cases.py`                                                    | fixed sequencing, no HTTP or planning                   |

No file owns more than one event-family grammar. `event_normalizer.py` may
dispatch but may not regain field-level family parsing.

## 8. Required Falsifiers

- a body hash mismatch reaches neither parser nor delivery claim;
- maximum accepted and maximum-plus-one resource cases are deterministic;
- duplicate JSON keys and surrogate failures do not echo payload data;
- a malformed payload never reserves the delivery key;
- a duplicate key emits neither a second seed nor observation;
- an actual task cancellation propagates unchanged;
- merge-group input cannot use a pull-request ref;
- an unadmitted action cannot become a planning seed;
- a workflow-run result preserves run, attempt, workflow, SHA, and status;
- an installed wheel exposes a byte-identical ingestion profile through
  `importlib.resources`;
- every reviewed event vector maps to the exact normalized result or explicit
  no-op outcome.

## 9. Non-Claims

This module does not verify HMAC signatures, receive HTTP bytes, persist a
delivery, decide repository policy, load a diff, issue a plan, publish a check,
reconcile a workflow, prove provider delivery freshness, or prove end-to-end
CPU and memory bounds before the HTTP boundary applies the same ingress profile.
