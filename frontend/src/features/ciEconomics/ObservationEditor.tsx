import { RotateCcw, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { configureObservation } from "../../api/ciEconomics/observationClient";
import {
  type ObservationCommand,
  type ObservationConfiguration,
  type ObservationSnapshot,
  observationCommandSchema,
  sameObservationConfiguration,
} from "../../api/ciEconomics/observationSchema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import type { WorkbenchScope } from "../../api/workbench/client";
import { ObservationWorkflowPicker } from "./ObservationWorkflowPicker";

const INITIAL: ObservationConfiguration = {
  enabled: false,
  selector: { kind: "all", workflowIds: null },
  backfillDays: 1,
};
type WriteState =
  | { readonly kind: "idle" }
  | { readonly kind: "pending" | "uncertain"; readonly command: ObservationCommand }
  | { readonly kind: "rejected"; readonly message: string };

export function ObservationEditor({
  scope,
  snapshot,
  session,
  stale,
  onSaved,
}: {
  readonly scope: WorkbenchScope;
  readonly snapshot: ObservationSnapshot | null;
  readonly session: ControlPlaneSession | undefined;
  readonly stale: boolean;
  readonly onSaved: () => void;
}) {
  const [base, setBase] = useState(snapshot);
  const [draft, setDraft] = useState(snapshot?.configuration ?? INITIAL);
  const [operationId, setOperationId] = useState(() => crypto.randomUUID());
  const [write, setWrite] = useState<WriteState>({ kind: "idle" });
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => controller.current?.abort(), []);
  const authorized = session?.roles.includes("configure") === true;
  const pending = write.kind === "pending";
  const unresolved = write.kind === "uncertain";
  const candidate = observationCommandSchema.safeParse({
    installationId: scope.installationId,
    repositoryId: scope.repositoryId,
    expectedRevision: base?.revision ?? 0,
    configuration: draft,
    operationId,
  });
  const changed = base === null || !sameObservationConfiguration(base.configuration, draft);
  const newer = (snapshot?.revision ?? 0) > (base?.revision ?? 0);
  function reload() {
    if (pending || unresolved || stale || (snapshot?.revision ?? 0) < (base?.revision ?? 0)) return;
    setBase(snapshot);
    setDraft(snapshot?.configuration ?? INITIAL);
    setOperationId(crypto.randomUUID());
    setWrite({ kind: "idle" });
  }
  async function save(command: ObservationCommand) {
    if (controller.current || !session || !authorized) return;
    const active = new AbortController();
    controller.current = active;
    setWrite({ kind: "pending", command });
    try {
      const result = await configureObservation(command, session.csrfToken, active.signal);
      if (active.signal.aborted) return;
      if (result.kind === "ready" && result.value.snapshot !== null) {
        setBase(result.value.snapshot);
        setDraft(result.value.snapshot.configuration);
        setOperationId(crypto.randomUUID());
        setWrite({ kind: "idle" });
        onSaved();
      } else if (result.kind === "ready") {
        setWrite({
          kind: "rejected",
          message:
            result.value.outcome === "capacity_reached"
              ? "The observation configuration limit has been reached."
              : "Configuration changed or the operation identity conflicts. Refresh and reload the saved configuration.",
        });
        onSaved();
      } else if (result.kind === "unauthenticated" || result.kind === "forbidden") {
        setWrite(
          write.kind === "uncertain"
            ? { kind: "uncertain", command }
            : {
                kind: "rejected",
                message:
                  "Configuration access was rejected. Sign in with an authorized administrator account.",
              },
        );
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
      aria-label="Observation configuration"
      onSubmit={(event) => {
        event.preventDefault();
        if (candidate.success && write.kind === "idle" && changed && !stale && !newer)
          void save(candidate.data);
      }}
    >
      <fieldset disabled={!authorized || stale || write.kind !== "idle"}>
        <legend>Observation configuration</legend>
        <div className="observation-fields">
          <label className="observation-toggle">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
            />
            Observation enabled
          </label>
          <label>
            Workflows
            <select
              value={draft.selector.kind}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  selector:
                    event.target.value === "all"
                      ? { kind: "all", workflowIds: null }
                      : { kind: "selected", workflowIds: [] },
                })
              }
            >
              <option value="all">All workflows</option>
              <option value="selected">Selected workflows</option>
            </select>
          </label>
          <label>
            Initial history
            <select
              value={draft.backfillDays}
              onChange={(event) => setDraft({ ...draft, backfillDays: Number(event.target.value) })}
            >
              <option value={0}>Recent runs only</option>
              {[1, 2, 3, 4, 5, 6].map((days) => (
                <option key={days} value={days}>
                  {days} {days === 1 ? "day" : "days"} before activation
                </option>
              ))}
            </select>
          </label>
        </div>
        {draft.selector.kind === "selected" ? (
          <ObservationWorkflowPicker
            scope={scope}
            selected={draft.selector.workflowIds ?? []}
            onChange={(ids) =>
              setDraft({ ...draft, selector: { kind: "selected", workflowIds: ids } })
            }
          />
        ) : null}
      </fieldset>
      <div className="economics-provenance">
        <span>Saved revision {base?.revision ?? 0}</span>
        <span>
          Pause stops new discovery; registered runs continue collecting. Retained evidence is not
          deleted.
        </span>
      </div>
      {newer ? (
        <p className="economics-prompt" role="status">
          A newer configuration is available. Your draft has not been replaced.
        </p>
      ) : null}
      {!authorized ? (
        <p className="economics-prompt" role="status">
          Configuration access required.
        </p>
      ) : null}
      {write.kind === "rejected" ? (
        <p className="economics-prompt" role="alert">
          {write.message}
        </p>
      ) : null}
      {unresolved ? (
        <p className="economics-prompt" role="alert">
          Save outcome unknown. Retry the same operation to confirm it before editing.
        </p>
      ) : null}
      <div className="economics-actions">
        <button
          type="submit"
          className="button button--compact"
          disabled={
            !authorized || !candidate.success || write.kind !== "idle" || stale || newer || !changed
          }
        >
          <Save className="button-icon" aria-hidden="true" />
          {pending ? "Saving" : "Save observation"}
        </button>
        {unresolved ? (
          <button
            type="button"
            className="button button--secondary"
            disabled={!authorized}
            onClick={() => void save(write.command)}
          >
            <RotateCcw className="button-icon" aria-hidden="true" />
            Retry same operation
          </button>
        ) : (
          <button
            type="button"
            className="button button--secondary"
            disabled={pending || stale || (snapshot?.revision ?? 0) < (base?.revision ?? 0)}
            onClick={reload}
          >
            <RotateCcw className="button-icon" aria-hidden="true" />
            Reload saved configuration
          </button>
        )}
      </div>
    </form>
  );
}
