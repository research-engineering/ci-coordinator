import { Eye, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ArchiveRead } from "../../api/ciEconomics/archiveReadSchema";
import {
  applyArchiveRetention,
  previewArchiveRetention,
} from "../../api/ciEconomics/archiveRetentionClient";
import {
  type RetentionPreview,
  type RetentionRequest,
  type RetentionSelection,
  retentionSelectionSchema,
} from "../../api/ciEconomics/archiveRetentionSchema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";

type State =
  | { readonly kind: "idle" }
  | { readonly kind: "previewing" }
  | {
      readonly kind: "review" | "applying" | "uncertain";
      readonly preview: RetentionPreview;
      readonly command: RetentionRequest;
    }
  | { readonly kind: "finished" | "rejected"; readonly message: string };

export function ArchiveRetention({
  page,
  defaultRevision,
  session,
  onChanged,
  onClose,
}: {
  readonly page: ArchiveRead;
  readonly defaultRevision: number;
  readonly session: ControlPlaneSession | undefined;
  readonly onChanged: () => void;
  readonly onClose: () => void;
}) {
  const record = page.records[0];
  const [state, setState] = useState<State>({ kind: "idle" });
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => controller.current?.abort(), []);
  if (record === undefined) return null;
  const detail = record.detail;
  const authorized = session?.roles.includes("configure") === true;
  async function preview(action: RetentionSelection["action"]) {
    if (!authorized || controller.current || record === undefined) return;
    const active = new AbortController();
    controller.current = active;
    setState({ kind: "previewing" });
    const { workflowRunId, runAttempt } = record.header.attempt;
    try {
      const selection = retentionSelectionSchema.parse({
        installationId: page.query.installationId,
        repositoryId: page.query.repositoryId,
        generation: page.query.generation,
        configurationRevision: page.configurationRevision,
        dataRevision: page.dataRevision,
        defaultRevision,
        importedThrough: page.observedAt,
        action,
        keys: [{ workflowRunId, runAttempt }],
      });
      const result = await previewArchiveRetention(selection, active.signal);
      if (active.signal.aborted) return;
      setState(
        result.kind === "ready"
          ? {
              kind: "review",
              preview: result.value,
              command: {
                selection,
                reviewedDigest: result.value.reviewedDigest,
                operationId: crypto.randomUUID(),
              },
            }
          : {
              kind: "rejected",
              message:
                "The preview could not be validated. Refresh the archive before reviewing again.",
            },
      );
    } catch {
      if (!active.signal.aborted)
        setState({
          kind: "rejected",
          message: "Preview unavailable. No retention change was requested.",
        });
    } finally {
      if (controller.current === active) controller.current = undefined;
    }
  }
  async function apply(
    review: Extract<State, { readonly kind: "review" | "applying" | "uncertain" }>,
  ) {
    if (!session || !authorized || controller.current) return;
    const active = new AbortController();
    controller.current = active;
    setState({ ...review, kind: "applying" });
    try {
      const result = await applyArchiveRetention(
        review.command,
        review.preview,
        session.csrfToken,
        active.signal,
      );
      if (active.signal.aborted) return;
      if (result.kind === "ready") {
        setState({
          kind: result.value.preview === null ? "rejected" : "finished",
          message:
            result.value.preview === null
              ? "The reviewed archive changed. No new change was admitted; refresh and review again."
              : "Retention change confirmed. Permanent statistics are preserved.",
        });
        onChanged();
      } else setState({ ...review, kind: "uncertain" });
    } catch {
      if (!active.signal.aborted) setState({ ...review, kind: "uncertain" });
    } finally {
      if (controller.current === active) controller.current = undefined;
    }
  }
  return (
    <section className="archive-retention" aria-label="Optional detail retention">
      <div className="economics-subheading">
        <h3>Optional detail retention</h3>
        <button
          type="button"
          className="button button--secondary"
          onClick={onClose}
          disabled={
            state.kind === "applying" || state.kind === "uncertain" || state.kind === "previewing"
          }
        >
          Close
        </button>
      </div>
      <dl>
        <dt>Detail content</dt>
        <dd>{detail.content.replaceAll("_", " ")}</dd>
        <dt>First detail import</dt>
        <dd>{detail.firstImportedAt ?? "Not imported"}</dd>
        <dt>Expiry</dt>
        <dd>{detail.expiresAt ?? "No finite expiry"}</dd>
      </dl>
      <p>
        Permanent run and job statistics are unaffected. Deleted details cannot be restored by
        changing retention.
      </p>
      {state.kind === "idle" ? (
        <div className="economics-actions">
          <button
            type="button"
            className="button button--secondary"
            disabled={!authorized}
            onClick={() => void preview("apply_policy")}
          >
            <Eye className="button-icon" aria-hidden="true" />
            Preview current policy
          </button>
          <button
            type="button"
            className="button button--secondary"
            disabled={!authorized}
            onClick={() => void preview("erase_details")}
          >
            <Trash2 className="button-icon" aria-hidden="true" />
            Preview detail deletion
          </button>
        </div>
      ) : null}
      {state.kind === "previewing" || state.kind === "applying" ? (
        <p role="status">
          {state.kind === "previewing" ? "Preparing preview" : "Applying reviewed change"}
        </p>
      ) : null}
      {"preview" in state ? (
        <section aria-label="Retention change review">
          <p>
            {state.preview.preview.deletedDetails} detail payloads will be deleted;{" "}
            {state.preview.preview.releasedBytes} bytes released. Statistics are preserved.
          </p>
          <ul>
            {state.preview.preview.effects.map((effect) => (
              <li key={`${effect.key.workflowRunId}:${effect.key.runAttempt}`}>
                Run #{effect.key.workflowRunId} / {effect.key.runAttempt}: {effect.before.state} to{" "}
                {effect.after.state}; expiry {effect.after.expiresAt ?? "none"}.
              </li>
            ))}
          </ul>
          {state.kind === "review" ? (
            <div className="economics-actions">
              <button
                type="button"
                className="button button--primary"
                disabled={!authorized}
                onClick={() => void apply(state)}
              >
                Confirm reviewed change
              </button>
              <button
                type="button"
                className="button button--secondary"
                onClick={() => setState({ kind: "idle" })}
              >
                Cancel
              </button>
            </div>
          ) : null}
          {state.kind === "uncertain" ? (
            <div role="alert">
              <p>
                The result is unknown. Retry this exact operation to confirm it; do not create
                another change.
              </p>
              <button
                type="button"
                className="button button--secondary"
                disabled={!authorized}
                onClick={() => void apply(state)}
              >
                <RefreshCw className="button-icon" aria-hidden="true" />
                Confirm existing operation
              </button>
            </div>
          ) : null}
        </section>
      ) : null}
      {state.kind === "finished" || state.kind === "rejected" ? (
        <p role="status">{state.message}</p>
      ) : null}
    </section>
  );
}
