import { Clock, Pause, Search } from "lucide-react";
import { observationMicroseconds } from "../../api/ciEconomics/observationSchema";
import type { ObservationStatus } from "../../api/ciEconomics/observationStatusSchema";
import { formatDateTime, formatInteger } from "../../domain/format";

export function ObservationProgress({ status }: { readonly status: ObservationStatus }) {
  const enabled = status.snapshot?.configuration.enabled === true;
  return (
    <section className="economics-subsection" aria-label="Scan progress">
      <dl className="economics-metrics">
        <div>
          <dt>Saved observation</dt>
          <dd>{status.snapshot === null ? "Not configured" : enabled ? "Enabled" : "Paused"}</dd>
        </div>
        <div>
          <dt>Source slots in use</dt>
          <dd>
            {formatInteger(status.occupiedSourceSlots)} / {formatInteger(status.maximumSourceSlots)}
            <small>Shared with manually registered runs</small>
          </dd>
        </div>
        <div>
          <dt>Observed at</dt>
          <dd>
            <time dateTime={status.observedAt}>{formatDateTime(status.observedAt)}</time>
          </dd>
        </div>
      </dl>
      {status.scans.length === 0 ? (
        <p className="economics-prompt">No observation scan has been configured.</p>
      ) : (
        <div className="observation-lanes">
          {status.scans.map((scan) => {
            const expiry =
              scan.leaseExpiresAt === null
                ? undefined
                : observationMicroseconds(scan.leaseExpiresAt);
            const observed = observationMicroseconds(status.observedAt);
            const claimed =
              enabled && expiry !== undefined && observed !== undefined && expiry > observed;
            return (
              <section
                key={scan.lane}
                aria-label={`${scan.lane === "recent" ? "Recent" : "Backfill"} scan`}
              >
                <h3>{scan.lane === "recent" ? "Recent runs" : "Initial backfill"}</h3>
                <p
                  className={`observation-lane-state${claimed ? " observation-lane-state--claimed" : ""}`}
                >
                  {!enabled ? (
                    <>
                      <Pause aria-hidden="true" />
                      Paused
                    </>
                  ) : claimed ? (
                    <>
                      <Search aria-hidden="true" />
                      Scan claimed
                    </>
                  ) : (
                    <>
                      <Clock aria-hidden="true" />
                      {scan.leaseExpiresAt ? "Awaiting claim recovery" : "Scheduled"}
                    </>
                  )}
                </p>
                <dl className="observation-details">
                  <div>
                    <dt>Pages recorded</dt>
                    <dd>{formatInteger(scan.pagesSeen)}</dd>
                  </div>
                  <div>
                    <dt>Sources registered</dt>
                    <dd>{formatInteger(scan.sourcesRegistered)}</dd>
                  </div>
                  <div>
                    <dt>Last recorded page</dt>
                    <dd>
                      {scan.lastPageAt === null
                        ? "Not yet recorded"
                        : formatDateTime(scan.lastPageAt)}
                    </dd>
                  </div>
                  <div>
                    <dt>Last recorded outcome</dt>
                    <dd>{scan.lastOutcome?.replaceAll("_", " ") ?? "Not yet recorded"}</dd>
                  </div>
                  <div>
                    <dt>Next eligible attempt</dt>
                    <dd>{enabled ? formatDateTime(scan.nextAttemptAt) : "Paused"}</dd>
                  </div>
                  {scan.leaseExpiresAt ? (
                    <div>
                      <dt>Claim expires</dt>
                      <dd>{formatDateTime(scan.leaseExpiresAt)}</dd>
                    </div>
                  ) : null}
                  {scan.window ? (
                    <div>
                      <dt>Current window / page {scan.pageNumber}</dt>
                      <dd>
                        {formatDateTime(scan.window.createdFrom)} to{" "}
                        {formatDateTime(scan.window.createdThrough)}
                      </dd>
                    </div>
                  ) : null}
                  <div>
                    <dt>Last completed window through</dt>
                    <dd>
                      {scan.lastCompletedThrough === null
                        ? "Not yet recorded"
                        : formatDateTime(scan.lastCompletedThrough)}
                    </dd>
                  </div>
                </dl>
              </section>
            );
          })}
        </div>
      )}
      <p className="economics-prompt">
        Recorded pages do not establish complete provider history. Runs are measured after
        collection; scan counts are not captured-run counts.
      </p>
      {status.detailTruncatedUntil ? (
        <p className="economics-prompt" role="status">
          Some gap details were evicted. Coverage remains incomplete until at least{" "}
          {formatDateTime(status.detailTruncatedUntil)}.
        </p>
      ) : null}
    </section>
  );
}
