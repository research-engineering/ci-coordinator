import { RotateCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { retryArchiveGaps } from "../../api/ciEconomics/archiveGapRepairClient";
import type {
  GapRepairInterval,
  GapRepairRequest,
} from "../../api/ciEconomics/archiveGapRepairSchema";
import type { ControlPlaneSession } from "../../api/controlPlaneIdentity/schema";

export interface GapRepairSelection {
  readonly request: GapRepairRequest;
  readonly intervals: readonly GapRepairInterval[];
}
type State = "ready" | "sending" | "uncertain" | "finished" | "rejected";

export function ArchiveGapRepair({
  selection,
  session,
  onChanged,
  onClose,
}: {
  readonly selection: GapRepairSelection;
  readonly session: ControlPlaneSession | undefined;
  readonly onChanged: () => void;
  readonly onClose: () => void;
}) {
  const [state, setState] = useState<State>("ready");
  const [message, setMessage] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const allowed = session?.roles.includes("configure") === true;
  const attempts = selection.intervals.reduce(
    (sum, item) => sum + item.throughAttempt - item.fromAttempt + 1,
    0,
  );
  async function submit() {
    if (!allowed || !session || controller.current || !["ready", "uncertain"].includes(state))
      return;
    const active = new AbortController();
    controller.current = active;
    setState("sending");
    try {
      const result = await retryArchiveGaps(
        selection.request,
        selection.intervals,
        session.csrfToken,
        active.signal,
      );
      if (active.signal.aborted) return;
      if (result.kind === "ready") {
        const accepted = result.value.receipt !== null;
        setState(accepted ? "finished" : "rejected");
        setMessage(
          accepted
            ? "Archive retry admission confirmed. Collection results remain pending."
            : `No new retry admitted: ${result.value.outcome.replaceAll("_", " ")}.`,
        );
        if (accepted) onChanged();
      } else {
        setState("uncertain");
        setMessage("The result is unknown. Retry the same request to recover its receipt.");
      }
    } catch {
      if (!active.signal.aborted) {
        setState("uncertain");
        setMessage("The result is unknown. Retry the same request to recover its receipt.");
      }
    } finally {
      if (controller.current === active) controller.current = null;
    }
  }
  return (
    <section className="economics-subsection" aria-label="Retry recorded gaps">
      <div className="economics-subheading">
        <h3>Retry recorded gaps</h3>
        <button
          type="button"
          className="icon-button"
          aria-label="Close retry selection"
          disabled={state === "sending" || state === "uncertain"}
          onClick={onClose}
        >
          <X aria-hidden="true" size={18} />
        </button>
      </div>
      <div className="archive-gap-repair-body">
        <p>
          {selection.intervals.length} {selection.intervals.length === 1 ? "run" : "runs"},{" "}
          {attempts} {attempts === 1 ? "attempt" : "attempts"}. Archive collection only.
        </p>
        <ul>
          {selection.intervals.map((item) => (
            <li key={item.workflowRunId}>
              #{item.workflowRunId}:{" "}
              {item.fromAttempt === item.throughAttempt
                ? `attempt ${item.fromAttempt}`
                : `attempts ${item.fromAttempt}-${item.throughAttempt}, inclusive`}
            </li>
          ))}
        </ul>
        {message ? (
          <p role={state === "uncertain" || state === "rejected" ? "alert" : "status"}>{message}</p>
        ) : null}
        {state === "ready" || state === "sending" || state === "uncertain" ? (
          <button
            type="button"
            className="button button--primary"
            disabled={!allowed || state === "sending"}
            onClick={() => void submit()}
          >
            <RotateCw aria-hidden="true" className="button-icon" />
            {state === "sending"
              ? "Submitting"
              : state === "uncertain"
                ? "Retry same request"
                : "Queue archive retries"}
          </button>
        ) : null}
      </div>
    </section>
  );
}
