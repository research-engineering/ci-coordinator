# History Stage Telemetry Implementation

Status: active implementation plan

Design: [stage telemetry](history-stage-telemetry.md).
Independent review follows the [repository policy](../../AGENTS.md).

1. Add one finite-label histogram and an exception-isolated context manager to
   `RuntimeMetrics`, using the existing library timer without a new clock port.
2. Wrap each claim, access, provider and completion await in the collection
   service. Preserve exact ordering, identity checks, abort handling, deadlines,
   lease transitions and the per-lane item bound. Pass lane through private
   shared read helpers, not through ambient state.
3. Add parameterized native observer controls for successful, exceptional and
   cancelled bodies, independent interleaved spans, hostile labels and timer
   startup/label/observation failures. Assert exact body execution and original
   exception identity. A controlled library clock proves positive elapsed time.
4. Extend native collection coverage with exact per-lane stage counts for page
   reads, attempts, handoff, skip, defer, denial and no due work. Keep every prior
   deadline, cancellation, fairness and semantic-outcome witness.
   Assert the empty stage set before a pre-claim abort and the exact operation
   prefix after claim, access or provider abort for all four lanes. Missing
   attempts retain completion spans in all three collection lanes; waiting
   retains them in both recheck lanes. Keep actual call/argument assertions so
   a fabricated sample cannot compensate for a missing operation.
5. Clarify RUNTIME015, add new proof routes without removing prior bindings and
   retain documentation reachability. Run configured static checks, one frozen
   review and exact native Full Check before squash.
6. Qualify and deploy the immutable successor to the owned development service;
   preserve the database and active import generation. Observe a bounded live
   interval before proposing a throughput change. Record incomplete evidence
   rather than substituting process elapsed time for CPU or production capacity.

No local behavioral tests, containers or servers. No pilot target workflow changes,
manual queue repair, new dependency, database migration or omission authority.
