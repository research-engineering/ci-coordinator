import { Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { purposeCategorySchema } from "../../api/ciEconomics/analyticsSchema";
import {
  configurePurposeSettings,
  fetchPurposeSettings,
} from "../../api/ciEconomics/purposeSettingsClient";
import {
  type PurposeSettingsCommand,
  type PurposeSettingsQuery,
  type PurposeSettingsSnapshot,
  purposeSettingsCommandSchema,
} from "../../api/ciEconomics/purposeSettingsSchema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";
import { EconomicsError, EconomicsRefresh } from "./EconomicsControls";
import { useEconomicsStatus } from "./useEconomicsStatus";

const noPoll = () => false;
type Entry = PurposeSettingsCommand["entries"][number];
type Row = {
  readonly key: string;
  readonly workflow: string;
  readonly job: string;
  readonly purposes: Entry["purposes"];
};
type Write =
  | { readonly kind: "idle" }
  | { readonly kind: "pending" | "uncertain"; readonly command: PurposeSettingsCommand };

type PurposeSettingsProps = {
  readonly query: PurposeSettingsQuery;
  readonly session: ControlPlaneSession | undefined;
  readonly active: boolean;
  readonly onSaved: () => void;
};

export function PurposeSettingsPanel(props: PurposeSettingsProps) {
  const { query, session } = props;
  const lifetime = JSON.stringify([
    query.installationId,
    query.repositoryId,
    query.generation,
    session?.user.actorId,
    session?.roles,
    session?.expiresAt,
  ]);
  return <ScopedPurposeSettingsPanel key={lifetime} {...props} />;
}

function ScopedPurposeSettingsPanel({ query, session, active, onSaved }: PurposeSettingsProps) {
  const key = JSON.stringify([query.installationId, query.repositoryId, query.generation]);
  const read = useCallback((signal: AbortSignal) => fetchPurposeSettings(query, signal), [query]);
  const { state, refresh } = useEconomicsStatus(key, read, noPoll, active);
  const [snapshot, setSnapshot] = useState<PurposeSettingsSnapshot>();
  useEffect(() => {
    if (state.value?.outcome === "available") setSnapshot(state.value.snapshot);
  }, [state.value]);
  return (
    <details className="purpose-settings">
      <summary>Analytics settings</summary>
      <EconomicsRefresh onClick={refresh} disabled={state.refreshing || !active} />
      {state.failure ? <EconomicsError failure={state.failure} onRetry={refresh} /> : null}
      {snapshot ? (
        <PurposeEditor
          key={key}
          snapshot={snapshot}
          session={session}
          stale={
            Boolean(state.failure) ||
            state.refreshing ||
            !active ||
            state.value?.outcome !== "available"
          }
          onSaved={() => {
            refresh();
            onSaved();
          }}
        />
      ) : state.value ? (
        <p role="status">
          Settings unavailable: {state.value.unavailable?.reason.replaceAll("_", " ")}. Refresh
          history.
        </p>
      ) : (
        <p role="status">
          {state.refreshing ? "Loading analytics settings" : "Analytics settings unavailable"}
        </p>
      )}
      {snapshot && state.value?.outcome === "unavailable" ? (
        <p role="alert">
          Settings are no longer current: {state.value.unavailable.reason.replaceAll("_", " ")}. The
          exact command is retained.
        </p>
      ) : null}
    </details>
  );
}

function rows(snapshot: PurposeSettingsSnapshot): Row[] {
  return (snapshot.mapping?.entries ?? []).map((entry) => ({
    key: crypto.randomUUID(),
    workflow: String(entry.workflowId),
    job: entry.jobName,
    purposes: entry.purposes,
  }));
}

function PurposeEditor({
  snapshot,
  session,
  stale,
  onSaved,
}: {
  readonly snapshot: PurposeSettingsSnapshot;
  readonly session: ControlPlaneSession | undefined;
  readonly stale: boolean;
  readonly onSaved: () => void;
}) {
  const [base, setBase] = useState(snapshot);
  const [draft, setDraft] = useState(() => rows(snapshot));
  const [operationId, setOperationId] = useState(() => crypto.randomUUID());
  const [write, setWrite] = useState<Write>({ kind: "idle" });
  const [notice, setNotice] = useState("");
  const [rejected, setRejected] = useState(false);
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => controller.current?.abort(), []);
  const authorized =
    session?.roles.includes("configure") === true && Date.parse(session.expiresAt) > Date.now();
  const newer = snapshot.revision > base.revision;
  const locked = write.kind !== "idle";
  const candidate = purposeSettingsCommandSchema.safeParse({
    installationId: base.installationId,
    repositoryId: base.repositoryId,
    generation: base.generation,
    expectedRevision: base.revision,
    operationId,
    entries: draft.map((entry) => ({
      workflowId: /^[1-9][0-9]{0,15}$/.test(entry.workflow) ? Number(entry.workflow) : 0,
      jobName: entry.job,
      purposes: entry.purposes,
    })),
  });
  const changed =
    base.mapping === null ||
    (candidate.success &&
      JSON.stringify(candidate.data.entries) !== JSON.stringify(base.mapping.entries));
  function edit(key: string, patch: Partial<Row>) {
    setDraft((previous) => previous.map((row) => (row.key === key ? { ...row, ...patch } : row)));
    setNotice("");
  }
  async function save(command: PurposeSettingsCommand) {
    if (
      !session ||
      !authorized ||
      stale ||
      controller.current ||
      (write.kind === "uncertain" && command !== write.command)
    )
      return;
    const prior = write;
    const current = new AbortController();
    controller.current = current;
    setWrite({ kind: "pending", command });
    setNotice("");
    try {
      const result = await configurePurposeSettings(command, session.csrfToken, current.signal);
      if (current.signal.aborted) return;
      if (Date.parse(session.expiresAt) <= Date.now()) {
        setWrite({ kind: "uncertain", command });
        return;
      }
      if (result.kind === "ready" && result.value.snapshot) {
        setBase(result.value.snapshot);
        setDraft(rows(result.value.snapshot));
        setOperationId(crypto.randomUUID());
        setWrite({ kind: "idle" });
        setRejected(false);
        setNotice(`Saved analytics settings, revision ${result.value.snapshot.revision}.`);
        onSaved();
      } else if (result.kind === "ready") {
        setWrite({ kind: "idle" });
        setRejected(true);
        setNotice(
          `Save rejected: ${result.value.outcome.replaceAll("_", " ")}. Reload saved settings.`,
        );
        onSaved();
      } else if (
        prior.kind !== "uncertain" &&
        (result.kind === "forbidden" || result.kind === "unauthenticated")
      ) {
        setWrite({ kind: "idle" });
        setRejected(true);
        setNotice("Configuration access rejected.");
      } else setWrite({ kind: "uncertain", command });
    } catch {
      if (!current.signal.aborted) setWrite({ kind: "uncertain", command });
    } finally {
      if (controller.current === current) controller.current = undefined;
    }
  }
  return (
    <form
      aria-label="Analytics purpose settings"
      onSubmit={(event) => {
        event.preventDefault();
        if (candidate.success && changed && !newer && !locked && !rejected)
          void save(candidate.data);
      }}
    >
      <p>
        Saved revision {base.revision}
        {base.mapping === null
          ? "; no mapping configured"
          : base.mapping.entries.length === 0
            ? "; empty mapping"
            : ""}
        . Generation {base.generation}.
      </p>
      <fieldset disabled={!authorized || stale || locked || rejected}>
        <legend>Exact workflow and job categories</legend>
        {draft.map((row, index) => (
          <div className="purpose-row" key={row.key}>
            <label>
              Workflow ID {index + 1}
              <input
                inputMode="numeric"
                value={row.workflow}
                onChange={(event) => edit(row.key, { workflow: event.target.value })}
                required
              />
            </label>
            <label>
              Exact job name {index + 1}
              <input
                value={row.job}
                maxLength={1024}
                onChange={(event) => edit(row.key, { job: event.target.value })}
                required
              />
            </label>
            <fieldset className="purpose-categories">
              <legend>Categories {index + 1}</legend>
              {purposeCategorySchema.options.map((category) => (
                <label key={category}>
                  <input
                    type="checkbox"
                    checked={row.purposes.includes(category)}
                    onChange={(event) =>
                      edit(row.key, {
                        purposes: event.target.checked
                          ? [...row.purposes, category]
                          : row.purposes.filter((value) => value !== category),
                      })
                    }
                  />
                  {category}
                </label>
              ))}
            </fieldset>
            <button
              type="button"
              className="icon-button"
              title={`Remove mapping ${index + 1}`}
              aria-label={`Remove mapping ${index + 1}`}
              onClick={() =>
                setDraft((previous) => previous.filter((item) => item.key !== row.key))
              }
            >
              <Trash2 aria-hidden="true" />
            </button>
          </div>
        ))}
        <button
          type="button"
          className="button button--secondary"
          disabled={draft.length >= 64}
          onClick={() =>
            setDraft((previous) => [
              ...previous,
              { key: crypto.randomUUID(), workflow: "", job: "", purposes: [] },
            ])
          }
        >
          <Plus className="button-icon" aria-hidden="true" />
          Add mapping
        </button>
      </fieldset>
      {!candidate.success ? (
        <p role="alert">
          Each mapping needs a positive workflow ID, an exact job name of 1-512 characters, and at
          least one category. Duplicate workflow/job pairs are not allowed.
        </p>
      ) : null}
      {!authorized ? <p role="status">Configuration access required.</p> : null}
      {newer ? <p role="status">Newer settings are available. Your draft is unchanged.</p> : null}
      {notice ? <p role={rejected ? "alert" : "status"}>{notice}</p> : null}
      {write.kind === "uncertain" ? (
        <p role="alert">Save outcome unknown. Retry the same operation before editing.</p>
      ) : null}
      <div className="economics-actions">
        <button
          className="button button--primary"
          type="submit"
          disabled={
            !authorized || stale || !candidate.success || !changed || newer || locked || rejected
          }
        >
          <Save className="button-icon" aria-hidden="true" />
          {write.kind === "pending" ? "Saving" : "Save analytics settings"}
        </button>
        {write.kind === "uncertain" ? (
          <button
            className="button button--secondary"
            type="button"
            disabled={!authorized || stale}
            onClick={() => void save(write.command)}
          >
            <RotateCcw className="button-icon" aria-hidden="true" />
            Retry same operation
          </button>
        ) : null}
        <button
          className="button button--secondary"
          type="button"
          disabled={stale || locked || snapshot.revision < base.revision}
          onClick={() => {
            setBase(snapshot);
            setDraft(rows(snapshot));
            setOperationId(crypto.randomUUID());
            setRejected(false);
            setNotice("");
          }}
        >
          <RotateCcw className="button-icon" aria-hidden="true" />
          Reload saved settings
        </button>
      </div>
    </form>
  );
}
