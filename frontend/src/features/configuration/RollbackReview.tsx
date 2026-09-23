import { Eye, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { RollbackCommand } from "../../api/configLifecycle/client";
import {
  type ConfigEpoch,
  type ConfigStatus,
  MAX_REASON_BYTES,
  rollbackCommandSchema,
} from "../../api/configLifecycle/schema";

export function RollbackReview({
  status,
  target,
  onSubmit,
}: {
  readonly status: ConfigStatus;
  readonly target: ConfigEpoch;
  readonly onSubmit: (command: RollbackCommand) => void;
}) {
  const [reason, setReason] = useState("");
  const [review, setReview] = useState<RollbackCommand>();
  const confirm = useRef<HTMLButtonElement>(null);
  const reasonInput = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (review) confirm.current?.focus();
  }, [review]);
  const active = status.active;
  if (!active || active.epochId === target.epochId) return null;
  const body = {
    schemaVersion: "ci-config-epoch-rollback/v1" as const,
    installationId: status.installationId,
    repositoryId: status.repositoryId,
    targetEpochId: target.epochId,
    expectedRevision: active.revision,
    operationId: "preview",
    reason,
  };
  const valid = rollbackCommandSchema.safeParse(body).success;
  return (
    <section className="configuration-review" aria-label="Rollback">
      <h3>Rollback</h3>
      <label htmlFor="configuration-rollback-reason">Reason</label>
      <textarea
        ref={reasonInput}
        id="configuration-rollback-reason"
        rows={3}
        value={reason}
        aria-invalid={reason.length > 0 && !valid}
        onChange={(event) => {
          setReason(event.target.value);
          setReview(undefined);
        }}
      />
      <p>
        {new TextEncoder().encode(reason).length} / {MAX_REASON_BYTES} UTF-8 bytes
      </p>
      {review ? (
        <>
          <h4>Confirm rollback</h4>
          <dl>
            <dt>Current epoch</dt>
            <dd>
              <code>{review.currentEpochId}</code>
            </dd>
            <dt>Current revision</dt>
            <dd>{review.body.expectedRevision}</dd>
            <dt>Target retained epoch</dt>
            <dd>
              <code>{review.body.targetEpochId}</code>
            </dd>
            <dt>Reason</dt>
            <dd>{review.body.reason}</dd>
          </dl>
          <p>
            Rollback changes the active configuration. The server must still prove non-reducing
            coverage and accept this revision.
          </p>
          <div className="configuration-actions">
            <button ref={confirm} type="button" className="button" onClick={() => onSubmit(review)}>
              <RotateCcw className="button-icon" aria-hidden="true" />
              Confirm rollback
            </button>
            <button
              type="button"
              className="button button--secondary"
              onClick={() => {
                setReview(undefined);
                reasonInput.current?.focus();
              }}
            >
              Cancel rollback
            </button>
          </div>
        </>
      ) : (
        <button
          type="button"
          className="button button--secondary"
          disabled={!valid}
          onClick={() => {
            if (valid)
              setReview({
                kind: "rollback",
                currentEpochId: active.epochId,
                body: { ...body, operationId: crypto.randomUUID() },
              });
          }}
        >
          <Eye className="button-icon" aria-hidden="true" />
          Review rollback
        </button>
      )}
    </section>
  );
}
