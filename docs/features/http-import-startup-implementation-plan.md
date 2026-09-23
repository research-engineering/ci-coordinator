# HTTP Import Startup Implementation Plan

Status: implementation plan

Design owner: [HTTP Import Startup](http-import-startup.md).

## Ordered Work

1. Freeze the base, namespace exports, internal import consumers, core
   requirements and HTTP mutation manifest. Retain earlier failed native
   attempts as diagnostic evidence, not repaired history.
2. Admit the namespace rule in the API HTTP specification and bind it to
   `REQ-CI-CORE-002`. Add routes for the design, plan, namespace and isolated
   package-import witness; preserve existing requirement routes.
3. Remove eager namespace re-exports. Rewrite only affected import statements
   to the current defining modules; retain explicit application composition.
   Verify that executable consumer bodies and imported symbol identities are
   unchanged. Do not widen this change to unrelated package facades.
4. Extend the existing package-import test with fresh-process namespace,
   middleware and explicit-application cases. Use an empty working directory,
   exact source root, isolated interpreter and finite process timeout. Record
   monotonic timing as diagnostics, not a product deadline.
5. Run admitted static format/lint, type, imports, requirements/Proofkit and
   documentation gates. Review the frozen design, plan, implementation and
   proof paths with the repository-selected independent reviewer; resolve
   material findings before native qualification and merge.
6. Obtain exact-head GitHub native tests, coverage and HTTP mutation evidence.
   Compare the HA01 process observations with the retained baseline; report
   runner and sampling limitations. A repeated timeout remains open work.
7. Publish and squash only after exact-head required checks pass. Qualify the
   new master separately; release/deployment require their own evidence.

## Acceptance

| Predicate                                   | Independent falsifier                                                                                                   |
|---------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Leaf imports do not compose the application | A cold namespace or middleware import reports `app` or any router module.                                               |
| Explicit composition remains possible       | The positive application-import control fails or fails to load `app`.                                                   |
| Internal migration is complete              | Type/import gates or native runtime/HTTP tests reject a retained consumer.                                              |
| Test semantics remain intact                | Changed executable test bodies, missing native identities, invalid baseline, surviving HTTP mutant or reduced coverage. |
| Routes and documentation remain current     | An unbound changed path, missing requirement route, broken link or unreachable new document.                            |

Do not close live session recovery, general performance, capacity, production
readiness or the remaining roadmap from this source-only optimization.
