# Partial History Capture Plan

The [design](partial-history-capture.md) owns semantics. This plan owns delivery
and proof routing, not a second population or storage contract.

1. Separate all observed job IDs from admitted terminal job statistics in the
   provider-owned page result. Keep exact run/head/attempt and malformed-input
   rejection before active-job omission.
2. Use observed IDs for page cardinality and duplicate checks across pages;
   retain current header bracketing and request/page/canonical-byte limits.
3. Update decoder tests for the explicit page vocabulary and add active-status,
   malformed-identity and contradictory-conclusion counterexamples.
4. Exercise the actual provider on mixed, all-active, empty, multi-page,
   duplicate skipped-ID and header-drift inputs. Assert retained population,
   exact request count and no extra provider effects after rejection.
5. Add a PostgreSQL partial-to-complete scenario preserving first import clocks,
   known operands and exact dataset accounting. Keep existing conflict/CAS and
   strict active-evidence witnesses mandatory.
6. Link the design, clarify the existing requirement only where necessary, and
   update every affected Proofkit route/binding and generated source projection.
7. Run allowed static gates, freeze the complete cohesive delta, use the
   repository's independent-review policy and native GitHub behavioral proofs.
   Group adjudicated repairs before another remote CI update.

Acceptance requires the design's S/T distinction to be independently falsified
by full active pages and skipped duplicates, not just a happy-path partial
result. Native success is not full-history completion, provider convergence,
production capacity or authority for selective omission. Deployment and live
pilot verification remain separate exact-source steps.
