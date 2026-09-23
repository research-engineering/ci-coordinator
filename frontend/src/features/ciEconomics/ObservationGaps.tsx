import { useCallback, useState } from "react";
import { fetchObservationGaps } from "../../api/ciEconomics/observationClient";
import type { WorkbenchScope } from "../../api/workbench/client";
import { IdentityDisclosure } from "../../components/IdentityDisclosure";
import { formatDateTime } from "../../domain/format";
import { EconomicsPagination, EconomicsRead, EconomicsRefresh } from "./EconomicsControls";
import { useEconomicsRead } from "./useEconomicsRead";

export function ObservationGaps({ scope }: { readonly scope: WorkbenchScope }) {
  const [page, setPage] = useState({ number: 1, cursor: null as string | null });
  const read = useCallback(
    (signal: AbortSignal) => fetchObservationGaps(scope, page.cursor, signal),
    [scope, page.cursor],
  );
  const gaps = useEconomicsRead(read);
  return (
    <section className="economics-subsection" aria-label="Observation coverage gaps">
      <div className="economics-subheading">
        <h3>Retained coverage gaps</h3>
        <EconomicsRefresh onClick={gaps.refresh} disabled={gaps.state.kind === "loading"} />
      </div>
      <EconomicsRead state={gaps.state} onRetry={gaps.refresh}>
        {(value) => (
          <>
            <ul className="economics-signal-list">
              {value.items.map((item) => (
                <li key={item.gapId}>
                  <div className="economics-subheading">
                    <h3>{item.gap.reason.replaceAll("_", " ")}</h3>
                    <span>
                      {item.gap.lane} / revision {item.gap.configRevision}
                    </span>
                  </div>
                  <div className="economics-provenance">
                    <span>
                      {formatDateTime(item.gap.createdFrom)} to{" "}
                      {formatDateTime(item.gap.createdThrough)}
                    </span>
                    <span>Retained until {formatDateTime(item.expiresAt)}</span>
                  </div>
                  <details>
                    <summary>Gap provenance</summary>
                    <dl className="observation-details">
                      <div>
                        <dt>Identity</dt>
                        <dd>
                          <IdentityDisclosure value={item.gapId} />
                        </dd>
                      </div>
                      <div>
                        <dt>Selector</dt>
                        <dd>
                          <IdentityDisclosure value={item.gap.selectorDigest} />
                        </dd>
                      </div>
                      <div>
                        <dt>Cycle started</dt>
                        <dd>{formatDateTime(item.gap.cycleStartedAt)}</dd>
                      </div>
                    </dl>
                  </details>
                </li>
              ))}
            </ul>
            {value.items.length === 0 ? (
              <p className="economics-prompt">
                No retained gaps on this page. This is not a complete-history guarantee.
              </p>
            ) : null}
          </>
        )}
      </EconomicsRead>
      <EconomicsPagination
        page={page.number}
        next={
          gaps.state.kind !== "loading" && gaps.state.result.kind === "ready"
            ? gaps.state.result.value.nextCursor
            : null
        }
        onFirst={() => setPage({ number: 1, cursor: null })}
        onNext={(cursor) => setPage((old) => ({ number: old.number + 1, cursor }))}
      />
    </section>
  );
}
