# CI Input, Property And Advisory Coverage

Status: accepted for the CI admission follow-up.
Owner: ci-coordinator.proofkit-adoption; image evidence remains with ci-coordinator.release.

## Problem And Scope

The existing matrix closes command identities and assigns repository paths to
surfaces. Assignment alone does not prove native tool input discovery, coverage
of every applicable property, or current knowledge about vulnerabilities in an
unchanged artifact. Its finite path-and-mode responsibility contract also needs
qualification before permitting a selective plan, rather than a later parity
failure in CI.

This decision refines the [comprehensive matrix](comprehensive-ci-matrix.md)
without replacing the native gate or introducing another requirement system.
The [supported runtime decision](lts-dependency-consolidation.md) continues to
own Python and Node versions. This also extends the
[CI trigger policy](../features/ci-trigger-policy.md) with a separate daily
03:23 UTC advisory workflow; its existing PR, merge-group and manual release
triggers remain unchanged.

## Decision

1. Before Go analysis, compare native Go package discovery with the admitted Git
   source inventory. Reject cgo, ignored, invalid, omitted, foreign or ambiguous
   source until a separate build-configuration owner admits it. Generation stays
   static; native discovery and its counterexamples run in the Go utility job.
2. Before using the self-consumer graph for omission, independently qualify its
   complete current head path-and-mode inventory from authenticated Git objects.
   Missing, stale, unsupported, truncated or incomplete evidence uses the existing
   untrusted-graph FullCI fallback. Ordinary content-only changes can preserve
   the admitted selective scope. A later generated-parity check is additional
   protection, not evidence for this preselection predicate.
3. Distinguish a surface's candidate commands from concrete declared tool inputs.
   Derive source roots and input lists from native owners; represent fixtures,
   generated data and special resources through explicit owner dispositions.
   The four existing font resources additionally bind the complete reviewed CSS
   source hash; CSS edits require renewed admission instead of inference from
   a URL in raw text. Unknown active roots fail admission. Static membership proves neither native
   execution nor arbitrary parser completeness.
4. Project the fixed blueprint candidate inventory into existing Proofkit
   requirements, exact assertion selectors, canonical command bindings and
   execution modes. Keep the 89 tool classes and 25 domain properties separate.
   Partial, deferred, unknown and inapplicable rows retain rationale, ownership
   and revision conditions. A structurally admitted projection is not a claim
   that all applicable properties have adequate or passing witnesses.
5. Recheck changing advisory feeds through a separate bounded scheduled workflow.
   Bind package checks to exact source and locks, and image checks to an immutable
   released digest and platform. Reuse the release vulnerability admission policy.
   Failure and unavailable evidence remain failures; no source diff is needed.
   The [periodic advisory procedure](../how-to/periodic-advisory-checks.md) defines
   the subject, 36-hour observation age and seven-day receipt retention. Native
   Actions failure notifications and retained receipts own this route.
   Scheduled execution availability and notification subscription are independent
   operational obligations, not proved by a workflow file.

## Alternatives And Consequences

Installing every blueprint utility would duplicate existing predicates and
leave these input and authority gaps unresolved. Broadening every command to
all files would mix independent roots and negative fixtures. Disabling all
selectivity would avoid the graph gap but remove an already bounded capability.
The selected route strengthens the existing owners with independent inputs and
causal falsifiers while preserving their execution and trust boundaries.

Fresh vulnerability knowledge has an external clock. Repeating Full Check daily
would spend resources on unrelated static and behavioral predicates. The
advisory workflow instead runs only applicable dependency and image scanners.
Its latest released image scope does not imply an inventory of all live or
historical deployments, and a hosted scheduler cannot certify its own absence
of missed runs. The operations procedure retains those limits explicitly.

## Revision Conditions

Revisit when build tags/platforms/cgo are admitted, source roots or tool discovery
semantics change, blueprint candidates or owned risks change, selective graph
provenance changes, or advisory sources, released-image topology, scan freshness
or notification obligations change. New proof claims require corresponding
assertions and GitHub qualification; a larger inventory alone cannot close them.
