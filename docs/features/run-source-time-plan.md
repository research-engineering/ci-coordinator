# Run Source Time Implementation

Design: [containing run time](run-source-time.md).
Requirements: `REQ-CI-RUNTIME-042` and preserved `REQ-CI-RUNTIME-045`.

1. Add the exact positive-safe run path and `ActionsClient.get_workflow_run`.
   Admit only its GET operation with the matching path and no query/body.
2. After validating the exact attempt, read and validate its containing run.
   Check repository/run/head equality and latest-attempt coverage; construct
   the original attempt source with containing-run time through one shared
   digest factory. Keep early malformed/failure/cancellation outcomes.
3. Add direct resolver/discovery equality, delayed-rerun and independent
   cross-resource identity falsifiers. Cover each provider edge and transport
   operand. Existing source-window, conflict and retention PostgreSQL tests
   remain unchanged rather than being weakened to accommodate the adapter.
4. Update requirements, compact proof routes and document navigation. Run
   admitted static checks and mutation-anchor preflight; obtain the independent
   review under `AGENTS.md` and exact native GitHub qualification before merge.
5. Deploy the qualified image and inspect real registration/report behavior
   before paired CI measurements. Preserve old conflicting records and report
   their actual state; do not repair them by changing their lifetime or identity.

Acceptance requires agreement of independently decoded discovery and explicit
sources for the same latest attempt, original time for older attempts, and
rejection of each substituted identity/time/transport operand. A third GET is
intentional and bounded; source tests do not establish its production latency
or certify every previously retained source.

The exact client-request oracle includes the existing API-version header.
`GitHubRequest` does not add that header automatically and compares it during
value equality; an expected request with empty headers contradicts the client
contract. Preserve the full request comparison rather than omitting headers.
