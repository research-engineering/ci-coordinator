# Synthetic Page Closure Plan

Design: [confirmed page closure](synthetic-page-closure.md).

1. Preserve foreign-origin admission and single per-page ownership. Subscribe
   to a bounded close-event wait before each supported close request.
2. Retain renderer closure, then attempt forced browser closure if needed.
   Do not equate acknowledgment or timeout with terminal success. If both
   confirmations fail, record the error, finish the session and close its
   owned context. Consume asynchronous failures through the existing observer.
3. Keep existing real-navigation assertions. Add deterministic acknowledged-
   without-effect probes for one and both close mechanisms; verify main-page
   survival on recovery, one sequence across repeated navigations and context
   destruction on exhaustion. Do not increase test timeouts or add retries.
   Observe the returned confirmation Promise directly while the close request
   remains pending; a close event alone cannot prove sequence completion.
   Require both real main-frame navigation commits while the page is open
   before using the request pair as evidence of single-sequence ownership.
   The existing confirmation function is module-visible for this direct oracle;
   no additional lifecycle abstraction or production API is introduced.
4. Link this successor to DEV016 and existing Proofkit routes. Preserve all
   pre-existing design and implementation-plan bytes.
5. Run local static admission, one bounded independent review, then the native
   browser suite and exact-head Full Check. Merge by squash only after native
   qualification. This development-only repair grants no production authority.

Writer readiness binds session closure and its native fixture as the only
behavioral owners. Protected observables are admitted origin, no egress,
synthetic isolation, reset disposal, visible main page after successful closure,
and explicit failure after exhausted closure. Acknowledgment, actual close,
deadline and session containment are independently falsified operands.
