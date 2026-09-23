import { RotateCcw, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { configureHistory } from "../../api/ciEconomics/historyClient";
import {
  type HistoryCommand,
  type HistoryConfiguration,
  type HistoryMutation,
  historyCommandSchema,
  sameHistoryConfiguration,
} from "../../api/ciEconomics/historySchema";
import type { HistoryStatus } from "../../api/ciEconomics/historyStatusSchema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { HistorySettings } from "./HistorySettings";
import { ObservationWorkflowPicker } from "./ObservationWorkflowPicker";

const INITIAL: HistoryConfiguration = {
  enabled: false,
  workflowIds: null,
  detailRetention: null,
  quota: { attempts: 10_000, jobs: 100_000, gaps: 1000, canonicalBytes: 268_435_456 },
};
type WriteState =
  | { readonly kind: "idle" }
  | { readonly kind: "pending" | "uncertain"; readonly command: HistoryCommand }
  | { readonly kind: "rejected"; readonly message: string };

export function HistoryEditor({
  scope,
  status,
  session,
  stale,
  onSaved,
}: {
  readonly scope: WorkbenchScope;
  readonly status: HistoryStatus;
  readonly session: ControlPlaneSession | undefined;
  readonly stale: boolean;
  readonly onSaved: () => void;
}) {
  const snapshot = status.snapshot;
  const [base, setBase] = useState(snapshot);
  const [draft, setDraft] = useState(snapshot?.configuration ?? INITIAL);
  const [initialDate, setInitialDate] = useState("");
  const [rescan, setRescan] = useState(false);
  const [operationId, setOperationId] = useState(() => crypto.randomUUID());
  const [write, setWrite] = useState<WriteState>({ kind: "idle" });
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => controller.current?.abort(), []);
  const authorized = session?.roles.includes("configure") === true;
  const pending = write.kind === "pending";
  const uncertain = write.kind === "uncertain";
  const fenced = snapshot?.state === "erasing" || snapshot?.state === "erased";
  const newer = (snapshot?.configurationRevision ?? 0) > (base?.configurationRevision ?? 0);
  const changed = base === null || rescan || !sameHistoryConfiguration(base.configuration, draft);
  const candidate = historyCommandSchema.safeParse({
    installationId: scope.installationId,
    repositoryId: scope.repositoryId,
    expectedRevision: base?.configurationRevision ?? 0,
    configuration: draft,
    initialCreatedFrom: base === null && initialDate ? `${initialDate}T00:00:00Z` : null,
    rescan,
    operationId,
  });
  const valid =
    candidate.success && (base !== null || initialDate <= status.observedAt.slice(0, 10));
  function reload() {
    if (
      pending ||
      uncertain ||
      stale ||
      (snapshot?.configurationRevision ?? 0) < (base?.configurationRevision ?? 0)
    )
      return;
    setBase(snapshot);
    setDraft(snapshot?.configuration ?? INITIAL);
    setRescan(false);
    setOperationId(crypto.randomUUID());
    setWrite({ kind: "idle" });
  }
  async function save(command: HistoryCommand) {
    if (controller.current || !session || !authorized || stale || fenced) return;
    const active = new AbortController();
    controller.current = active;
    setWrite({ kind: "pending", command });
    try {
      const result = await configureHistory(command, session.csrfToken, active.signal);
      if (active.signal.aborted) return;
      if (result.kind === "ready" && result.value.snapshot !== null) {
        setBase(result.value.snapshot);
        setDraft(result.value.snapshot.configuration);
        setRescan(false);
        setOperationId(crypto.randomUUID());
        setWrite({ kind: "idle" });
        onSaved();
      } else if (result.kind === "ready") {
        setWrite({ kind: "rejected", message: rejectionMessage(result.value.outcome) });
        onSaved();
      } else if ((result.kind === "unauthenticated" || result.kind === "forbidden") && !uncertain) {
        setWrite({
          kind: "rejected",
          message:
            "Configuration access was rejected. Sign in with an authorized administrator account.",
        });
      } else setWrite({ kind: "uncertain", command });
    } catch {
      if (!active.signal.aborted) setWrite({ kind: "uncertain", command });
    } finally {
      if (controller.current === active) controller.current = undefined;
    }
  }
  return (
    <form
      className="observation-editor"
      aria-label="Historical collection configuration"
      onSubmit={(event) => {
        event.preventDefault();
        if (candidate.success && valid && changed && !newer && write.kind === "idle")
          void save(candidate.data);
      }}
    >
      <fieldset disabled={!authorized || stale || fenced || write.kind !== "idle"}>
        <legend>Historical collection</legend>
        <div className="observation-fields history-editor-fields">
          <label className="observation-toggle">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
            />
            Collection enabled
          </label>
          {base === null ? (
            <label>
              Import runs created since (UTC)
              <input
                type="date"
                required
                value={initialDate}
                max={status.observedAt.slice(0, 10)}
                onChange={(event) => setInitialDate(event.target.value)}
              />
            </label>
          ) : (
            <label className="observation-toggle">
              <input
                type="checkbox"
                checked={rescan}
                onChange={(event) => setRescan(event.target.checked)}
              />
              Rescan the configured history
            </label>
          )}
          <label>
            Workflows
            <select
              value={draft.workflowIds === null ? "all" : "selected"}
              onChange={(event) =>
                setDraft({ ...draft, workflowIds: event.target.value === "all" ? null : [] })
              }
            >
              <option value="all">All workflows</option>
              <option value="selected">Selected workflows</option>
            </select>
          </label>
        </div>
        {draft.workflowIds !== null ? (
          <ObservationWorkflowPicker
            scope={scope}
            selected={draft.workflowIds}
            onChange={(ids) => setDraft({ ...draft, workflowIds: ids })}
          />
        ) : null}
        <HistorySettings
          configuration={draft}
          defaultPolicy={status.defaults.detailRetention}
          onChange={setDraft}
        />
        <p className="economics-provenance">
          Enabling detailed records applies to future imports. It does not revisit summary-only
          records; use the explicit rescan to retry their first import. Expired detail is not
          restored.
        </p>
      </fieldset>
      <p className="economics-provenance">
        Saved revision {base?.configurationRevision ?? 0}. Pausing preserves recorded statistics.
        Rescanning reconciles existing identities without duplicating them.
      </p>
      {newer ? (
        <p role="status">A newer configuration is available. Your draft has not been replaced.</p>
      ) : null}
      {fenced ? (
        <p role="status">This archive is {snapshot?.state}; configuration is unavailable.</p>
      ) : null}
      {!authorized ? <p role="status">Configuration access required.</p> : null}
      {write.kind === "rejected" ? <p role="alert">{write.message}</p> : null}
      {uncertain ? (
        <p role="alert">Save outcome unknown. Retry the same operation before editing.</p>
      ) : null}
      <div className="economics-actions">
        <button
          type="submit"
          className="button button--compact"
          disabled={
            !authorized || stale || fenced || !valid || !changed || newer || write.kind !== "idle"
          }
        >
          <Save className="button-icon" aria-hidden="true" />
          {pending ? "Saving" : "Save history"}
        </button>
        {uncertain ? (
          <button
            type="button"
            className="button button--secondary"
            disabled={!authorized || stale || fenced}
            onClick={() => void save(write.command)}
          >
            Retry same operation
          </button>
        ) : null}
        <button
          type="button"
          className="button button--secondary"
          disabled={pending || uncertain || stale}
          onClick={reload}
        >
          <RotateCcw className="button-icon" aria-hidden="true" />
          Reload saved configuration
        </button>
      </div>
    </form>
  );
}

function rejectionMessage(outcome: HistoryMutation["outcome"]): string {
  if (outcome === "invalid_population")
    return "The initial date is outside the admitted population. Reload and choose an earlier date.";
  if (outcome === "capacity_reached") return "The archive dataset limit has been reached.";
  if (outcome === "dataset_fenced")
    return "This archive is being erased or has already been erased.";
  return "Configuration or operation identity changed. Refresh and reload the saved configuration.";
}
