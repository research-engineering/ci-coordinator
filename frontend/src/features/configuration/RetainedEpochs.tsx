import { ChevronLeft, ChevronRight, Download, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { RollbackCommand } from "../../api/configLifecycle/client";
import type { ConfigEpoch, ConfigScope, ConfigStatus } from "../../api/configLifecycle/schema";
import { exportConfigSource } from "../../api/configLifecycle/source";
import type { LifecycleFailure } from "../../api/configLifecycle/transport";
import { ConfigurationNotice } from "./ConfigurationNotice";
import { RollbackReview } from "./RollbackReview";
import { useConfigurationStatus } from "./useConfigurationStatus";

export function RetainedEpochs({
  scope,
  active,
  minimumActive,
  readRevision,
  canRollback,
  onRollback,
}: {
  readonly scope: ConfigScope;
  readonly active: boolean;
  readonly minimumActive: ConfigStatus["active"] | undefined;
  readonly readRevision: number;
  readonly canRollback: boolean;
  readonly onRollback: (command: RollbackCommand) => void;
}) {
  const query = useConfigurationStatus(scope, active, minimumActive, readRevision);
  return (
    <section aria-label="Retained configuration epochs">
      <div className="configuration-toolbar">
        <h2>Retained epochs</h2>
        <button
          type="button"
          className="icon-button"
          title="Refresh epochs"
          aria-label="Refresh epochs"
          disabled={!active}
          onClick={query.refresh}
        >
          <RefreshCw aria-hidden="true" />
        </button>
      </div>
      {!active ? null : !query.result ? (
        <p role="status">Loading retained epochs</p>
      ) : query.result.kind !== "ready" ? (
        <ConfigurationNotice failure={query.result} />
      ) : (
        <>
          <EpochPage
            key={query.key}
            status={query.result.value}
            scope={scope}
            canRollback={canRollback}
            onRollback={onRollback}
          />
          <nav className="configuration-actions" aria-label="Epoch pages">
            <button
              type="button"
              className="icon-button"
              title="Previous epoch page"
              aria-label="Previous epoch page"
              disabled={query.page === 1}
              onClick={query.previous}
            >
              <ChevronLeft aria-hidden="true" />
            </button>
            <span>Page {query.page}</span>
            <button
              type="button"
              className="icon-button"
              title="Next epoch page"
              aria-label="Next epoch page"
              disabled={query.result.value.nextCursor === null}
              onClick={query.next}
            >
              <ChevronRight aria-hidden="true" />
            </button>
          </nav>
        </>
      )}
    </section>
  );
}

function EpochPage({
  status,
  scope,
  canRollback,
  onRollback,
}: {
  readonly status: ConfigStatus;
  readonly scope: ConfigScope;
  readonly canRollback: boolean;
  readonly onRollback: (command: RollbackCommand) => void;
}) {
  const [selected, setSelected] = useState<ConfigEpoch>();
  return (
    <>
      <p role="status">
        {status.active ? `Active revision ${status.active.revision}` : "No active configuration"}
      </p>
      <details className="configuration-identities">
        <summary>Active identity</summary>
        <code>{status.active?.epochId ?? "None"}</code>
      </details>
      {status.epochs.length === 0 ? (
        <p>No retained epochs on this page.</p>
      ) : (
        <div className="configuration-table-scroll">
          <table>
            <caption>Retained configurations</caption>
            <thead>
              <tr>
                <th scope="col">Epoch</th>
                <th scope="col">Format</th>
                <th scope="col">Bytes</th>
                <th scope="col">State</th>
              </tr>
            </thead>
            <tbody>
              {status.epochs.map((epoch) => (
                <tr key={epoch.epochId} aria-selected={selected?.epochId === epoch.epochId}>
                  <th scope="row">
                    <button
                      type="button"
                      className="configuration-epoch-link"
                      aria-pressed={selected?.epochId === epoch.epochId}
                      aria-label={`Inspect epoch ${epoch.epochId.slice(0, 12)}`}
                      onClick={() => setSelected(epoch)}
                    >
                      {epoch.epochId.slice(0, 12)}
                    </button>
                  </th>
                  <td>{epoch.sourceFormat}</td>
                  <td>{epoch.sourceByteCount.toLocaleString("en-US")}</td>
                  <td>{epoch.epochId === status.active?.epochId ? "Active" : "Retained"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selected ? (
        <EpochDetail
          key={selected.epochId}
          scope={scope}
          status={status}
          epoch={selected}
          canRollback={canRollback}
          onRollback={onRollback}
        />
      ) : null}
    </>
  );
}

function EpochDetail({
  scope,
  status,
  epoch,
  canRollback,
  onRollback,
}: {
  readonly scope: ConfigScope;
  readonly status: ConfigStatus;
  readonly epoch: ConfigEpoch;
  readonly canRollback: boolean;
  readonly onRollback: (command: RollbackCommand) => void;
}) {
  const [failure, setFailure] = useState<LifecycleFailure>();
  const [busy, setBusy] = useState(false);
  const [downloaded, setDownloaded] = useState(false);
  const controller = useRef<AbortController | undefined>(undefined);
  const objectUrl = useRef<string | undefined>(undefined);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
    return () => {
      controller.current?.abort();
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    };
  }, []);
  async function download() {
    if (controller.current) return;
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    setFailure(undefined);
    setDownloaded(false);
    const result = await exportConfigSource(scope, epoch, request.signal);
    if (request.signal.aborted) return;
    controller.current = undefined;
    setBusy(false);
    if (result.kind !== "ready") {
      setFailure(result);
      return;
    }
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    const url = URL.createObjectURL(result.value);
    objectUrl.current = url;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `configuration-${epoch.epochId}.${epoch.sourceFormat === "json" ? "json" : "yaml"}`;
    anchor.click();
    setDownloaded(true);
  }
  return (
    <section className="configuration-detail" aria-label="Selected epoch">
      <h3 ref={heading} tabIndex={-1}>
        Selected epoch
      </h3>
      <dl>
        <dt>Epoch</dt>
        <dd>
          <code>{epoch.epochId}</code>
        </dd>
        <dt>Source hash</dt>
        <dd>
          <code>{epoch.sourceHash}</code>
        </dd>
        <dt>Document hash</dt>
        <dd>
          <code>{epoch.documentHash}</code>
        </dd>
        <dt>Compiled hash</dt>
        <dd>
          <code>{epoch.epochHash}</code>
        </dd>
      </dl>
      <button
        type="button"
        className="button button--secondary"
        disabled={busy}
        onClick={() => void download()}
      >
        <Download className="button-icon" aria-hidden="true" />
        {busy ? "Verifying source" : "Download source"}
      </button>
      {failure ? <ConfigurationNotice failure={failure} /> : null}
      {downloaded ? <p role="status">Verified source download started.</p> : null}
      {canRollback ? (
        <RollbackReview status={status} target={epoch} onSubmit={onRollback} />
      ) : (
        <p>Activate role required for rollback.</p>
      )}
    </section>
  );
}
