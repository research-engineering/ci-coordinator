# Recover Webhook Deliveries

Use this procedure after an authorized operator has confirmed the correct
GitHub App and a working, admitted destination. Before any request, obtain
the administrator-owned authorization for that exact endpoint and deployment.
An existing hold remains binding until its owner explicitly clears it; a
successful observation elsewhere cannot clear the hold. Consult the
[roadmap](../../ROADMAP.md) for recorded deployment evidence; this reusable
procedure neither declares a live endpoint state nor authorizes a request.

## Identify The Missing Effect

1. Preserve the alert interval, service correlation identity when available,
   affected App, repository and expected consumer. Do not copy webhook payloads,
   authorization headers, signatures or credentials into logs or tickets.
2. Check `CIWebhookDeliveryUnavailable`, readiness and scrape health. Absence of
   the webhook alert is not proof of delivery: failures before the application
   may not produce an HTTP metric.
3. Use the App's recent delivery history to find the event and all attempts with
   its delivery GUID. Record the numeric attempt ID separately. Never substitute
   one identifier for the other or assume a previous failed attempt is still
   unresolved after a successful redelivery.
4. Apply the [consumer matrix](../features/webhook-recovery-boundaries.md#current-consumer-matrix).
   A seed acknowledgement does not promise a plan. Registered executions may
   still converge through REST; missing job timing can remain unknown.

## Restore The Delivery

1. Diagnose the recorded failure before retrying. Correct only the admitted
   connectivity, credential, resource or storage cause. Do not raise body limits,
   disable signature validation, purge deduplication or manufacture a new GUID.
2. After the administrator clears the destination, use GitHub's
   [App redelivery procedure](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks#redelivering-github-app-webhooks)
   for the exact event. GitHub's documented window is three days. If no retained
   attempt exists, stop and record the gap instead of reconstructing JSON.
3. Read the new attempt's completed result. An accepted redelivery request is
   only scheduling acknowledgement, not proof that the receiver processed it.
4. If the outcome is ambiguous, inspect delivery history and durable consumer
   evidence before another attempt. Do not use an unconditional retry loop.
   A conflict or persistent rejection requires investigation, not altered bytes.

## Verify The Consumer

| Expected effect           | Required readback                                                                                                                               |
|---------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| Delivery claim only       | Successful processed or exact-duplicate classification for that event; no queued work is inferred.                                              |
| Completed job observation | Exact repository, run, attempt, head and job identity in economics evidence; verify the relevant timing quality instead of assuming `exact`.    |
| Existing reconciliation   | The exact registered subject's durable result, within its retained deadline/attempt policy; a different run cannot close it.                    |
| Future planning event     | Not implemented by this delivery path. Use the independently authenticated plan-request flow; do not report event-driven planning as recovered. |

The metrics and delivery views are diagnostic. Use the authenticated product
read surface and retained audit evidence for product conclusions. If a needed
readback is unavailable, keep that part of the incident unresolved. Neither
HTTP 200 nor an alert returning to normal establishes every row above.

## Close Or Escalate

Close only the effects whose current authority, successful delivery and
consumer readback are established. Preserve partial/unknown telemetry when an
old event cannot be recovered. Starting another workflow creates a new subject;
it cannot fill the original event history.

Escalate repeated failures, insufficient operator capacity or a recovery
objective that cannot fit the provider window. Automated recovery then requires
the separately governed provider-effect lifecycle; an App private key or raw
payload archive is not a substitute for that design.
