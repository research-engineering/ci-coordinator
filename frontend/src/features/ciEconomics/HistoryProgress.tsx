import { Database, ListChecks, ScanLine, TriangleAlert } from "lucide-react";
import type { HistoryStatus } from "../../api/ciEconomics/historyStatusSchema";
import { formatDateTime, formatInteger } from "../../domain/format";
import { retentionLabel } from "./HistorySettings";

export function HistoryProgress({ status }: { readonly status: HistoryStatus }) {
  const { snapshot, scan, discovery, effectiveDetailRetention } = status;
  if (snapshot === null || scan === null)
    return <p className="economics-prompt">No historical collection configured.</p>;
  const metrics = [
    { label: "Retained attempts", value: snapshot.usage.attempts, Icon: Database },
    { label: "Retained jobs", value: snapshot.usage.jobs, Icon: ListChecks },
    { label: "Provider pages checked", value: scan.pagesSeen, Icon: ScanLine },
    { label: "Recorded coverage gaps", value: snapshot.usage.gaps, Icon: TriangleAlert },
  ];
  return (
    <section className="history-progress" aria-label="Historical collection progress">
      <dl className="history-metrics">
        {metrics.map(({ label, value, Icon }) => (
          <div key={label}>
            <dt>
              <Icon aria-hidden="true" />
              {label}
            </dt>
            <dd>{formatInteger(value)}</dd>
          </div>
        ))}
      </dl>
      <div className="economics-provenance">
        <span>Collection: {snapshot.state}</span>
        <span>Initial traversal: {scan.traversalComplete ? "finished" : "pending"}</span>
        <span>Pending rechecks: {formatInteger(status.pendingRechecks)}</span>
        <span>Last outcome: {scan.lastOutcome?.replaceAll("_", " ") ?? "none recorded"}</span>
      </div>
      <details className="history-advanced">
        <summary>Scan and storage details</summary>
        <dl className="history-timeline">
          <div>
            <dt>Requested range (UTC)</dt>
            <dd>
              <HistoryTime value={scan.createdFrom} /> to{" "}
              <HistoryTime value={scan.createdThrough} />
            </dd>
          </div>
          <div>
            <dt>Current window (UTC)</dt>
            <dd>
              <HistoryTime value={scan.windowFrom} /> to <HistoryTime value={scan.windowThrough} />,
              page {scan.pageNumber}
            </dd>
          </div>
          <div>
            <dt>Attempts visited</dt>
            <dd>{formatInteger(scan.attemptsSeen)}</dd>
          </div>
          {discovery ? (
            <>
              <div>
                <dt>Recent recovery through</dt>
                <dd>
                  {discovery.completedThrough ? (
                    <HistoryTime value={discovery.completedThrough} />
                  ) : (
                    "Not yet completed"
                  )}
                </dd>
              </div>
              <div>
                <dt>Recent discovery pages checked</dt>
                <dd>{formatInteger(discovery.progress.pagesSeen)}</dd>
              </div>
              <div>
                <dt>Discovered runs awaiting handoff</dt>
                <dd>{formatInteger(discovery.pendingRuns)}</dd>
              </div>
            </>
          ) : null}
          <div>
            <dt>Canonical data</dt>
            <dd>
              {formatInteger(snapshot.usage.canonicalBytes)} /{" "}
              {formatInteger(snapshot.configuration.quota.canonicalBytes)} bytes
            </dd>
          </div>
          <div>
            <dt>Next eligible attempt</dt>
            <dd>
              <HistoryTime value={scan.nextAttemptAt} />
            </dd>
          </div>
          {scan.leaseExpiresAt ? (
            <div>
              <dt>Work allocation expires</dt>
              <dd>
                <HistoryTime value={scan.leaseExpiresAt} />
              </dd>
            </div>
          ) : null}
          {effectiveDetailRetention ? (
            <div>
              <dt>Effective detail policy</dt>
              <dd>
                {retentionLabel(effectiveDetailRetention.policy)} (
                {effectiveDetailRetention.source.replaceAll("_", " ")})
              </dd>
            </div>
          ) : null}
          <div>
            <dt>Status observed</dt>
            <dd>
              <HistoryTime value={status.observedAt} />
            </dd>
          </div>
        </dl>
      </details>
      <p className="economics-prompt">
        Finished traversal does not prove complete provider history. Gaps and unavailable runs
        remain explicit.
      </p>
    </section>
  );
}

function HistoryTime({ value }: { readonly value: string }) {
  return (
    <time dateTime={value} title={value}>
      {formatDateTime(value)}
    </time>
  );
}
